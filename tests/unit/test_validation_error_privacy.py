import asyncio
import json

from starlette.requests import Request

from app.api.errors import request_validation_error_handler
from fastapi.exceptions import RequestValidationError


def test_validation_error_response_does_not_echo_rejected_private_values_or_context():
    marker = "PRIVATE_VALIDATION_MARKER_7f92"
    error = {
        "type": "string_type",
        "loc": ("body", "profile", marker),
        "msg": f"Rejected value: {marker}",
        "input": marker,
        "ctx": {"error": ValueError(f"provider rejected {marker}")},
    }
    exc = RequestValidationError([error], body={"summary": marker})
    request = Request({
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/candidate/profile",
        "raw_path": b"/api/v1/candidate/profile",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    })

    response = asyncio.run(request_validation_error_handler(request, exc))
    payload = json.loads(response.body)

    assert response.status_code == 422
    assert marker not in response.body.decode("utf-8")
    assert "provider rejected" not in response.body.decode("utf-8")
    assert payload["detail"][0]["type"] == "string_type"
    assert "msg" not in payload["detail"][0]
    assert "loc" not in payload["detail"][0]
    assert "input" not in payload["detail"][0]
    assert "ctx" not in payload["detail"][0]
