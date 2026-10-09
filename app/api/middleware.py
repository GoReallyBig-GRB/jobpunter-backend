import json
import uuid
from typing import Any, Awaitable, Callable

from fastapi import Request

ASGIReceive = Callable[[], Awaitable[dict[str, Any]]]
ASGISend = Callable[[dict[str, Any]], Awaitable[None]]


async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


class RequestBodyLimitMiddleware:
    """Enforce an actual streamed-body limit, including missing/false Content-Length."""

    def __init__(self, app, max_bytes: int = 100_000):
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: ASGIReceive, send: ASGISend):
        if scope["type"] != "http":
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
