import pytest
from fastapi.testclient import TestClient

from app.application.ai.schemas import GenerateOptions, GenerateRequest, GenerateResponse
from app.main import app


def test_ai_request_contract_rejects_unknown_fields():
    with pytest.raises(ValueError):
        GenerateRequest.model_validate({
            "task": "cover_letter",
            "prompt": "Draft a cover letter",
            "model": "candidate-selected-model",
        })


def test_ai_request_contract_bounds_prompt_and_tokens():
    with pytest.raises(ValueError):
        GenerateRequest(task="cover_letter", prompt="x" * 80_001)
    with pytest.raises(ValueError):
        GenerateOptions(maxTokens=4097)


def test_ai_request_contract_rejects_unknown_task():
    with pytest.raises(ValueError):
        GenerateRequest(task="invented_task", prompt="hello")


def test_ai_response_uses_frontend_expected_aliases():
    response = GenerateResponse(
        rawText='{"ok": true}',
        parsedData={"ok": True},
        modelUsed="test-model",
        providerUsed="google",
        latencyMs=12,
    )
    assert response.model_dump(by_alias=True) == {
        "rawText": '{"ok": true}',
        "parsedData": {"ok": True},
        "modelUsed": "test-model",
        "providerUsed": "google",
        "latencyMs": 12,
        "isFallback": False,
        "confidence": "MEDIUM",
    }


def test_versioned_health_contract_remains_stable():
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"data": {"status": "ok"}}


def test_ai_route_requires_authentication_before_execution():
    client = TestClient(app)
    response = client.post(
        "/api/v1/ai/generate",
        json={"task": "cover_letter", "prompt": "Write a grounded cover letter"},
    )
    assert response.status_code == 401
