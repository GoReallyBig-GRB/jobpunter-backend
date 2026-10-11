import json
import uuid
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request

ASGIReceive = Callable[[], Awaitable[dict[str, Any]]]
ASGISend = Callable[[dict[str, Any]], Awaitable[None]]


def parse_cors_origins(raw: str | None) -> list[str]:
    """Parse an explicit origin allow-list; reject wildcard and non-origin URLs."""
    if not raw or not raw.strip():
        return []

    origins: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        if "*" in value:
            raise ValueError("CORS origins must be explicit; wildcard origins are not allowed")
        try:
            parsed = urlsplit(value)
            # Accessing .port also validates malformed port values.
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Invalid CORS origin") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("CORS entries must be HTTP(S) origins without paths or credentials")
        origin = f"{parsed.scheme}://{parsed.netloc.lower()}"
        if origin not in seen:
            origins.append(origin)
            seen.add(origin)
    return origins


async def request_id_middleware(request: Request, call_next):
    supplied_request_id = request.headers.get("X-Request-ID", "")
    # Never reflect arbitrary client input into a response header or audit context.
    request_id = (
        supplied_request_id
        if len(supplied_request_id) <= 128
        and supplied_request_id.isascii()
        and all(char.isalnum() or char in "._:-" for char in supplied_request_id)
        and supplied_request_id
        else str(uuid.uuid4())
    )
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


class SecurityHeadersMiddleware:
    """Add baseline browser security headers to normal and early-rejection responses."""

    HEADERS = (
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    )

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: ASGIReceive, send: ASGISend):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _ in headers}
                for name, value in self.HEADERS:
                    if name not in existing:
                        headers.append((name, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


class RequestBodyLimitMiddleware:
    """Enforce an actual streamed-body limit, including missing/false Content-Length."""

    def __init__(self, app, max_bytes: int = 100_000):
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: ASGIReceive, send: ASGISend):
        if scope["type"] != "http" or not (
            scope.get("method") == "POST" and scope.get("path") == "/api/v1/ai/generate"
        ):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        declared = headers.get(b"content-length")
        if declared:
            try:
                declared_size = int(declared)
            except ValueError:
                await self._reject(send, 400, "INVALID_CONTENT_LENGTH", "Invalid Content-Length header.")
                return
            if declared_size < 0:
                await self._reject(send, 400, "INVALID_CONTENT_LENGTH", "Invalid Content-Length header.")
                return
            if declared_size > self.max_bytes:
                await self._reject(send, 413, "REQUEST_TOO_LARGE", "Request body is too large.")
                return

        chunks: list[bytes] = []
        total = 0
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                await self._reject(send, 413, "REQUEST_TOO_LARGE", "Request body is too large.")
                return
            chunks.append(chunk)
            more_body = message.get("more_body", False)

        body = b"".join(chunks)
        sent = False

        async def replay_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(send: ASGISend, status: int, code: str, message: str) -> None:
        payload = json.dumps({"detail": {"code": code, "message": message}}).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": payload})
