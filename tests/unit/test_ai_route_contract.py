from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_principal
from app.api.v1 import ai as ai_routes
from app.application.ai.schemas import GenerateResponse
from app.infrastructure.ai import google_generation
from app.infrastructure.ai.google_generation import AIProviderFailure, generate_with_google
from app.main import app
from app.ports.identity import AuthenticatedPrincipal


def test_authenticated_ai_route_returns_frontend_contract(monkeypatch):
    principal = AuthenticatedPrincipal(user_id=uuid4(), auth_provider="test")
    app.dependency_overrides[get_principal] = lambda: principal

    async def fake_execute(payload):
        assert payload.task == "cover_letter"
        assert payload.prompt == "Use only documented evidence"
        return GenerateResponse(
            rawText="A grounded draft",
            parsedData=None,
            modelUsed="test-model",
            providerUsed="google",
            latencyMs=7,
        )

    monkeypatch.setattr(ai_routes, "execute_ai_task", fake_execute)
    try:
        response = TestClient(app).post(
            "/api/v1/ai/generate",
            json={
                "task": "cover_letter",
                "prompt": "Use only documented evidence",
                "options": {"jsonMode": False, "maxTokens": 512},
            },
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)

    assert response.status_code == 200
    assert response.json() == {
        "rawText": "A grounded draft",
        "parsedData": None,
        "modelUsed": "test-model",
        "providerUsed": "google",
        "latencyMs": 7,
        "isFallback": False,
        "confidence": "MEDIUM",
    }


def test_ai_route_rejects_oversized_declared_request():
    app.dependency_overrides[get_principal] = lambda: AuthenticatedPrincipal(
        user_id=uuid4(), auth_provider="test"
    )
    try:
        response = TestClient(app).post(
            "/api/v1/ai/generate",
            headers={"Content-Length": "100001", "Content-Type": "application/json"},
            content='{"task":"cover_letter","prompt":"x"}',
        )
    finally:
        app.dependency_overrides.pop(get_principal, None)

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "REQUEST_TOO_LARGE"


def test_ai_provider_rejects_empty_response(monkeypatch):
    class FakeModels:
        async def generate_content(self, **_kwargs):
            return type("Response", (), {"text": None})()

    fake_client = type("Client", (), {
        "aio": type("Aio", (), {"models": FakeModels()})()
    })()
    monkeypatch.setattr(google_generation.genai, "Client", lambda **_kwargs: fake_client)
    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_API_KEY", "test-key")
    monkeypatch.setenv("JOBPUNTER_AI_PROVIDER", "google")
    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_MODEL", "gemini-test")

    with pytest.raises(AIProviderFailure, match="empty response"):
        import asyncio

        asyncio.run(
            generate_with_google(
                task="cover_letter",
                prompt="Draft only from evidence",
                schema_description=None,
                json_mode=False,
                max_tokens=200,
            )
        )


def test_task_policy_separator_is_actual_newline(monkeypatch):
    class FakeModels:
        def __init__(self):
            self.call = None

        async def generate_content(self, **kwargs):
            self.call = kwargs
            return type("Response", (), {"text": "draft"})()

    models = FakeModels()
    fake_client = type("Client", (), {
        "aio": type("Aio", (), {"models": models})()
    })()
    monkeypatch.setattr(google_generation.genai, "Client", lambda **_kwargs: fake_client)
    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_API_KEY", "test-key")
    monkeypatch.setenv("JOBPUNTER_AI_PROVIDER", "google")
    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_MODEL", "gemini-test")

    import asyncio

    asyncio.run(
        generate_with_google(
            task="evidence_matching",
            prompt="Candidate and job data",
            schema_description=None,
            json_mode=False,
            max_tokens=200,
        )
    )

    instruction = models.call["config"].system_instruction
    assert "\n\nTask policy:" in instruction
    assert "\\n" not in instruction
