from contextlib import contextmanager

import httpcore
import httpx
import pytest

from app.infrastructure.acquisition import public_http


def _answer(address):
    import ipaddress
    import socket

    parsed = ipaddress.ip_address(address)
    family = socket.AF_INET6 if parsed.version == 6 else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (address, 443))]


def test_connection_backend_connects_to_the_validated_ip_literal(monkeypatch):
    from httpcore._backends.sync import SyncBackend

    monkeypatch.setattr(public_http.socket, "getaddrinfo", lambda *args, **kwargs: _answer("93.184.216.34"))
    calls = []

    def fake_connect(self, host, port, timeout=None, local_address=None, socket_options=None):
        calls.append((host, port))
        return object()

    monkeypatch.setattr(SyncBackend, "connect_tcp", fake_connect)
    public_http.PublicAddressSyncBackend().connect_tcp("jobs.example.com", 443)

    assert calls == [("93.184.216.34", 443)]


def test_connection_backend_rejects_mixed_public_and_private_dns_answers(monkeypatch):
    import socket

    monkeypatch.setattr(
        public_http.socket,
        "getaddrinfo",
        lambda *args, **kwargs: _answer("93.184.216.34") + _answer("127.0.0.1"),
    )

    with pytest.raises(httpcore.ConnectError, match="non-public"):
        public_http.PublicAddressSyncBackend().connect_tcp("rebind.example", 443)


def test_default_http_transport_uses_public_address_backend():
    transport = public_http.PublicAddressHTTPTransport()
    try:
        assert isinstance(transport._pool._network_backend, public_http.PublicAddressSyncBackend)
    finally:
        transport.close()


def test_bounded_get_stops_before_retaining_more_than_the_limit():
    class Response:
        def raise_for_status(self):
            return None

        def iter_bytes(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield b"1234"
            yield b"5678"
            yield b"9"

    class Client:
        @contextmanager
        def stream(self, method, url, *, params=None, headers=None, follow_redirects=None):
            assert method == "GET"
            assert follow_redirects is False
            yield Response()

    with pytest.raises(public_http.ResponseTooLargeError):
        public_http.bounded_get(Client(), "https://jobs.example.com", max_bytes=8)



def test_bounded_get_rejects_clients_that_cannot_stream():
    class BufferedOnlyClient:
        def get(self, url):
            raise AssertionError("buffered get must never be called")

    with pytest.raises(httpx.UnsupportedProtocol, match="must support streaming"):
        public_http.bounded_get(BufferedOnlyClient(), "https://jobs.example.com", max_bytes=8)


def test_bounded_get_rejects_followed_redirects():
    class Response:
        history = [object()]

        def raise_for_status(self):
            raise AssertionError("redirect history must be checked first")

    class Client:
        @contextmanager
        def stream(self, method, url, *, params=None, headers=None, follow_redirects=None):
            assert follow_redirects is False
            yield Response()

    with pytest.raises(httpx.HTTPError, match="Redirects are not permitted"):
        public_http.bounded_get(Client(), "https://jobs.example.com", max_bytes=8)
