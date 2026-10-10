import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.ai import generate_ai
from app.application.ai.schemas import GenerateRequest


def make_request(body: bytes, headers=()):
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/ai/generate",
            "headers": list(headers),
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "scheme": "http",
        },
        receive,
    )


def test_ai_endpoint_rejects_oversized_actual_body_without_content_length(monkeypatch):
    called = {"value": False}

    async def forbidden_execution(*_args, **_kwargs):
        called["value"] = True
        raise AssertionError("Oversized request must be rejected before AI execution.")

    monkeypatch.setattr("app.api.v1.ai.execute_ai_task", forbidden_execution)
    request = make_request(b"x" * 100_001)
    payload = GenerateRequest(task="cv_generation", prompt="Generate from approved evidence.")

    with pytest.raises(HTTPException) as error:
        asyncio.run(generate_ai(
            payload,
            request,
            principal=type("Principal", (), {"user_id": uuid4()})(),
            db=object(),
        ))

    assert error.value.status_code == 413
    assert error.value.detail["code"] == "REQUEST_TOO_LARGE"
    assert called["value"] is False


def test_ai_endpoint_accepts_body_at_configured_byte_limit_when_execution_succeeds(monkeypatch):
    async def fake_execution(payload, *, db, user_id, request_id):
        return {
            "rawText": "ok",
            "parsedData": None,
            "modelUsed": "test-model",
            "providerUsed": "google",
            "latencyMs": 1,
            "isFallback": False,
            "confidence": "MEDIUM",
        }

    monkeypatch.setattr("app.api.v1.ai.execute_ai_task", fake_execution)
    request = make_request(b"x" * 100_000)
    payload = GenerateRequest(task="cv_generation", prompt="Generate from approved evidence.")

    result = asyncio.run(generate_ai(
        payload,
        request,
        principal=type("Principal", (), {"user_id": uuid4()})(),
        db=object(),
    ))
    assert result.raw_text == "ok"
