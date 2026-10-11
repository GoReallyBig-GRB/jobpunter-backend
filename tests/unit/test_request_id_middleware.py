import asyncio
import re

from fastapi import Response
from starlette.requests import Request

from app.api.middleware import request_id_middleware


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request({
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    })


def _run(headers: list[tuple[bytes, bytes]]) -> Response:
    async def execute():
        async def call_next(_request):
            return Response("ok")

        return await request_id_middleware(_request(headers), call_next)

    return asyncio.run(execute())


def test_request_id_accepts_safe_bounded_client_value():
    response = _run([(b"x-request-id", b"trace-123.a_B:9")])
    assert response.headers["X-Request-ID"] == "trace-123.a_B:9"


def test_request_id_replaces_non_ascii_value_with_generated_uuid():
    response = _run([(b"x-request-id", b"bad-\xff-value")])
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        response.headers["X-Request-ID"],
    )


def test_request_id_replaces_overlong_value():
    response = _run([(b"x-request-id", b"a" * 129)])
    assert len(response.headers["X-Request-ID"]) == 36


def test_request_id_generates_value_when_missing():
    response = _run([])
    assert len(response.headers["X-Request-ID"]) == 36
