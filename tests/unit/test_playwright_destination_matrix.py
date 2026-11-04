"""L1.5: Playwright only ever reaches allow-listed public hosts; every hostile destination class is refused."""
import socket

import pytest

from app.infrastructure.acquisition.playwright import _host_is_allowed, _public_address_for_host

ALLOWED = {"jobs.example.com"}

HOSTILE_URLS = [
    "http://127.0.0.1/admin", "http://[::1]/admin", "http://10.0.0.5/x", "http://192.168.1.1/",
    "http://169.254.169.254/latest/meta-data/", "http://localhost/", "http://service.internal/",
    "file:///etc/passwd", "ftp://jobs.example.com/x", "javascript:alert(1)", "data:text/html,<h1>x</h1>",
    "https://jobs.example.com:8443/x",              # non-standard port on an allowed host
    "https://user:pw@jobs.example.com/x",           # credentials in URL
    "https://jobs.example.com./x",                  # trailing-dot host variant
    "https://jobs.example.com.evil.example/x",      # suffix confusion
    "https://evil.example/jobs.example.com",        # allowed host only in the path
    "//127.0.0.1/protocol-relative", "",
]


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_hostile_destination_is_never_allowed(url):
    assert _host_is_allowed(url, ALLOWED) is False


def test_the_configured_public_host_itself_is_allowed():
    assert _host_is_allowed("https://jobs.example.com/careers?page=2", ALLOWED) is True
    assert _host_is_allowed("https://JOBS.example.com/careers", ALLOWED) is True


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "10.1.2.3", "192.168.0.9", "169.254.169.254",
                                  "localhost", "db.internal", "printer.local", "x.lan", "", "fe80::1"])
def test_non_public_hosts_are_refused_before_launch(host):
    with pytest.raises(ValueError):
        _public_address_for_host(host)


@pytest.mark.parametrize("answer", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.5"])
def test_dns_rebinding_to_a_private_answer_is_refused(monkeypatch, answer):
    family = socket.AF_INET6 if ":" in answer else socket.AF_INET
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, port, *a, **k: [(family, socket.SOCK_STREAM, 6, "", (answer, port))])
    with pytest.raises(ValueError):
        _public_address_for_host("jobs.example.com")


def test_one_private_answer_among_public_answers_is_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", port)),
    ])
    with pytest.raises(ValueError):
        _public_address_for_host("jobs.example.com")
