"""Public-address-pinned HTTP transport and bounded response reading.

The transport validates DNS answers at connection time and opens the TCP socket to
the selected IP literal. TLS still receives the original request hostname from
httpcore, preserving SNI and certificate hostname validation.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any

import httpcore
import httpx
from httpcore._backends.sync import SyncBackend


class ResponseTooLargeError(ValueError):
    """Raised before a response body can exceed its configured memory bound."""


def _public_address(host: str, port: int) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise httpcore.ConnectError("Public hostname resolution failed") from exc
        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        try:
            for answer in answers:
                addresses.add(ipaddress.ip_address(answer[4][0]))
        except (IndexError, TypeError, ValueError) as exc:
            raise httpcore.ConnectError("Hostname returned an invalid address") from exc
        if not addresses or any(not item.is_global for item in addresses):
            raise httpcore.ConnectError("Hostname resolved to a non-public address")
        return sorted(addresses, key=lambda item: (item.version, int(item)))[0]

    if not address.is_global:
        raise httpcore.ConnectError("Request targeted a non-public address")
    return address


class PublicAddressSyncBackend(SyncBackend):
    """Resolve and validate once, then connect to the IP literal to prevent DNS rebinding."""

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ):
        address = _public_address(host, port)
        return super().connect_tcp(
            str(address),
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class PublicAddressHTTPTransport(httpx.HTTPTransport):
    """HTTPX transport whose direct TCP connections are pinned to validated public IPs."""

    def __init__(self) -> None:
        super().__init__(trust_env=False)
        pool = getattr(self, "_pool", None)
        if pool is None or not hasattr(pool, "_network_backend"):
            raise RuntimeError("Unsupported HTTPX/httpcore transport internals")
        pool._network_backend = PublicAddressSyncBackend()


def bounded_get(
    client: Any,
    url: str,
    *,
    max_bytes: int,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    """GET a response using streaming only, bounding bytes retained in memory.

    Clients must implement the streaming interface. Falling back to `get()` would
    buffer an unbounded response before this function could enforce the byte limit.
    """
    stream = getattr(client, "stream", None)
    if not callable(stream):
        raise httpx.UnsupportedProtocol("HTTP client must support streaming responses")

    with stream("GET", url, params=params, headers=headers) as response:
        if getattr(response, "history", ()):
            raise httpx.HTTPError("Redirects are not permitted")
        response.raise_for_status()
        body = bytearray()
        for chunk in response.iter_bytes(chunk_size=64 * 1024):
            if len(body) + len(chunk) > max_bytes:
                raise ResponseTooLargeError("Response exceeded configured byte limit")
            body.extend(chunk)
        return bytes(body)
