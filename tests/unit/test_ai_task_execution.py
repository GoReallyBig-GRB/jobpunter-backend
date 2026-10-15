import asyncio
import pytest
from fastapi.testclient import TestClient

from app.application.ai.schemas import GenerateOptions, GenerateRequest, GenerateResponse
from app.infrastructure.ai import google_generation
from app.infrastructure.ai.google_generation import (
    AIProviderFailure,
    AIProviderNotConfigured,
    InvalidStructuredOutput,
    generate_with_google,
)
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


def test_ai_provider_fails_closed_without_server_key(monkeypatch):
    monkeypatch.delenv("JOBPUNTER_GOOGLE_AI_API_KEY", raising=False)
    monkeypatch.setenv("JOBPUNTER_AI_PROVIDER", "google")
    with pytest.raises(AIProviderNotConfigured):
        asyncio.run(generate_with_google(
            task="cover_letter",
            prompt="Write a grounded cover letter",
            schema_description=None,
            json_mode=False,
            max_tokens=200,
        ))


def test_ai_provider_applies_server_owned_task_policy(monkeypatch):
    class FakeModels:
        def __init__(self):
            self.call = None

        async def generate_content(self, **kwargs):
            self.call = kwargs
            return type("Response", (), {"text": "grounded output"})()

    models = FakeModels()
    fake_client = type("Client", (), {
        "aio": type("Aio", (), {"models": models})()
    })()
    monkeypatch.setattr(google_generation.genai, "Client", lambda **_kwargs: fake_client)
    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_API_KEY", "test-key")
    monkeypatch.setenv("JOBPUNTER_AI_PROVIDER", "google")
    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_MODEL", "gemini-test")

    asyncio.run(generate_with_google(
        task="evidence_matching",
        prompt="Candidate and job data",
        schema_description=None,
        json_mode=False,
        max_tokens=200,
    ))

    instruction = models.call["config"].system_instruction
    assert "Keyword overlap alone is not proof" in instruction
    assert "deterministic hard constraints" in instruction
    assert "Never invent candidate facts" in instruction


def test_ai_provider_executes_and_parses_structured_output(monkeypatch):
    class FakeModels:
        def __init__(self):
            self.call = None

        async def generate_content(self, **kwargs):
            self.call = kwargs
            return type("Response", (), {"text": '{"answer": "grounded"}'})()

    models = FakeModels()
    fake_client = type("Client", (), {
        "aio": type("Aio", (), {"models": models})()
    })()
    monkeypatch.setattr(google_generation.genai, "Client", lambda **_kwargs: fake_client)
    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_API_KEY", "test-key")
    monkeypatch.setenv("JOBPUNTER_AI_PROVIDER", "google")
    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_MODEL", "gemini-test")

    result = asyncio.run(generate_with_google(
        task="cover_letter",
        prompt="Use only the candidate evidence.",
        schema_description='{"answer": "string"}',
        json_mode=True,
        max_tokens=123,
    ))

    assert result.raw_text == '{"answer": "grounded"}'
    assert result.parsed_data == {"answer": "grounded"}
    assert result.model_used == "gemini-test"
    assert result.provider_used == "google"
    assert models.call["config"].temperature == 0.2
    assert models.call["config"].max_output_tokens == 123
    assert models.call["config"].response_mime_type == "application/json"


def test_ai_provider_maps_upstream_errors_to_safe_failure(monkeypatch):
    class FakeModels:
        async def generate_content(self, **_kwargs):
            raise RuntimeError("private upstream details")

    fake_client = type("Client", (), {
        "aio": type("Aio", (), {"models": FakeModels()})()
    })()
    monkeypatch.setattr(google_generation.genai, "Client", lambda **_kwargs: fake_client)
    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_API_KEY", "test-key")
    monkeypatch.setenv("JOBPUNTER_AI_PROVIDER", "google")
    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_MODEL", "gemini-test")

    with pytest.raises(AIProviderFailure) as caught:
        asyncio.run(generate_with_google(
            task="cover_letter",
            prompt="test",
            schema_description=None,
            json_mode=False,
            max_tokens=200,
        ))
    assert "private upstream details" not in str(caught.value)


def test_ai_provider_rejects_invalid_structured_output(monkeypatch):
    class FakeModels:
        async def generate_content(self, **_kwargs):
            return type("Response", (), {"text": "not-json"})()

    fake_client = type("Client", (), {
        "aio": type("Aio", (), {"models": FakeModels()})()
    })()
    monkeypatch.setattr(google_generation.genai, "Client", lambda **_kwargs: fake_client)
    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_API_KEY", "test-key")
    monkeypatch.setenv("JOBPUNTER_AI_PROVIDER", "google")
    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_MODEL", "gemini-test")

    with pytest.raises(InvalidStructuredOutput):
        asyncio.run(generate_with_google(
            task="cover_letter",
            prompt="test",
            schema_description='{"answer": "string"}',
            json_mode=True,
            max_tokens=200,
        ))


def test_published_task_config_reads_only_published_version_and_validates_payload():
    from types import SimpleNamespace
    from app.application.ai.service import _published_task_config

    published = SimpleNamespace(
        config_key="ai.task.cover_letter",
        status="PUBLISHED",
        payload={"model": "gemini-2.5-flash", "temperature": 0.3, "system_instruction": "Keep drafts concise."},
    )

    class FakeDB:
        def scalar(self, statement):
            self.statement = statement
            return published

    selected, payload = _published_task_config(FakeDB(), "cover_letter")
    assert selected is published
    assert payload["temperature"] == 0.3
    assert payload["system_instruction"] == "Keep drafts concise."


def test_published_task_config_fails_closed_on_unapproved_model():
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.application.ai.service import _published_task_config

    row = SimpleNamespace(
        config_key="ai.task.cover_letter",
        status="PUBLISHED",
        payload={"model": "unapproved-provider-model"},
    )

    class FakeDB:
        def scalar(self, statement):
            return row

    with pytest.raises(HTTPException) as caught:
        _published_task_config(FakeDB(), "cover_letter")
    assert caught.value.status_code == 503


def test_published_ai_overrides_keep_baseline_policy(monkeypatch):
    class FakeModels:
        def __init__(self):
            self.call = None
        async def generate_content(self, **kwargs):
            self.call = kwargs
            return type("Response", (), {"text": "grounded output"})()

    models = FakeModels()
    fake_client = type("Client", (), {"aio": type("Aio", (), {"models": models})()})()
    monkeypatch.setattr(google_generation.genai, "Client", lambda **_kwargs: fake_client)
    monkeypatch.setenv("JOBPUNTER_GOOGLE_AI_API_KEY", "test-key")
    monkeypatch.setenv("JOBPUNTER_AI_PROVIDER", "google")

    asyncio.run(generate_with_google(
        task="evidence_matching",
        prompt="Candidate and job data",
        schema_description=None,
        json_mode=False,
        max_tokens=200,
        model_override="gemini-governed-test",
        task_instruction_override="Prefer concise rationale.",
        temperature_override=0.7,
    ))

    config = models.call["config"]
    assert models.call["model"] == "gemini-governed-test"
    assert config.temperature == 0.7
    assert "Keyword overlap alone is not proof" in config.system_instruction
    assert "Prefer concise rationale." in config.system_instruction
    assert "Never invent candidate facts" in config.system_instruction


def test_execution_uses_published_config_and_records_its_version(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    from app.application.ai.schemas import GenerateRequest
    from app.application.ai.service import execute_ai_task
    from app.infrastructure.ai.google_generation import ProviderResult

    config_id = uuid4()
    published = SimpleNamespace(
        id=config_id,
        config_key="ai.task.cover_letter",
        status="PUBLISHED",
        payload={
            "model": "gemini-governed-test",
            "temperature": 0.4,
            "system_instruction": "Keep the draft concise and evidence-grounded.",
        },
    )

    class FakeDB:
        def __init__(self):
            self.added = []
            self.commit_count = 0

        def scalar(self, _statement):
            return published

        def add(self, value):
            self.added.append(value)

        def commit(self):
            self.commit_count += 1

        def refresh(self, _value):
            pass

    calls = {}

    async def fake_generate(**kwargs):
        calls.update(kwargs)
        return ProviderResult(
            raw_text='{"content": "A concise, evidence-grounded draft.", "missing_information": [], "unsupported_claims": []}',
            parsed_data={
                "content": "A concise, evidence-grounded draft.",
                "missing_information": [],
                "unsupported_claims": [],
            },
            model_used="gemini-governed-test",
            provider_used="google",
            latency_ms=9,
        )

    monkeypatch.setattr("app.application.ai.service.get_ai_provider", lambda _provider: type("FakeAdapter", (), {"generate": staticmethod(fake_generate)})())
    db = FakeDB()
    response = asyncio.run(
        execute_ai_task(
            GenerateRequest(task="cover_letter", prompt='{"job_title":"Product Manager","employer":"Example Ltd","job_description":"Responsibilities include owning product discovery, translating customer problems into clear product requirements, coordinating engineers and designers, managing roadmaps, prioritizing delivery, defining success metrics, and communicating trade-offs to stakeholders. Qualifications include demonstrated product management experience, analytical problem solving, technical fluency with software platforms and APIs, strong written communication, and experience delivering measurable outcomes across cross-functional teams.","approved_candidate_evidence":[{"id":"ev-1","type":"experience","content":"Owned product delivery and stakeholder management.","revision":1}],"instruction":"Write a concise cover letter grounded only in supplied evidence."}'),
            db=db,
            user_id=uuid4(),
            request_id="governed-ai-test",
        )
    )

    execution = next(value for value in db.added if value.__class__.__name__ == "AIExecution")
    assert execution.config_version_id == config_id
    assert execution.output_contract_version == "v1"
    assert execution.status == "SUCCEEDED"
    assert execution.request_id == "governed-ai-test"
    assert calls["model_override"] == "gemini-governed-test"
    assert calls["temperature_override"] == 0.4
    assert calls["task_instruction_override"] == "Keep the draft concise and evidence-grounded."
    assert response.raw_text == "A concise, evidence-grounded draft."
    assert db.commit_count == 2


def test_failed_execution_retains_output_contract_version_and_failure_category(monkeypatch):
    from uuid import uuid4

    from fastapi import HTTPException

    from app.application.ai.schemas import GenerateRequest
    from app.application.ai.service import execute_ai_task

    class FakeDB:
        def __init__(self):
            self.added = []
            self.commit_count = 0

        def scalar(self, _statement):
            return None

        def add(self, value):
            self.added.append(value)

        def commit(self):
            self.commit_count += 1

        def refresh(self, _value):
            pass

    async def fail_generate(**_kwargs):
        raise AIProviderFailure("private provider response details")

    monkeypatch.setattr("app.application.ai.service.get_ai_provider", lambda _provider: type("FakeAdapter", (), {"generate": staticmethod(fail_generate)})())
    db = FakeDB()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            execute_ai_task(
                GenerateRequest(task="cover_letter", prompt='{"job_title":"Product Manager","employer":"Example Ltd","job_description":"Responsibilities include owning product discovery, translating customer problems into clear product requirements, coordinating engineers and designers, managing roadmaps, prioritizing delivery, defining success metrics, and communicating trade-offs to stakeholders. Qualifications include demonstrated product management experience, analytical problem solving, technical fluency with software platforms and APIs, strong written communication, and experience delivering measurable outcomes across cross-functional teams.","approved_candidate_evidence":[{"id":"ev-1","type":"experience","content":"Owned product delivery and stakeholder management.","revision":1}],"instruction":"Write a concise cover letter grounded only in supplied evidence."}'),
                db=db,
                user_id=uuid4(),
                request_id="failed-contract-version-test",
            )
        )

    execution = next(value for value in db.added if value.__class__.__name__ == "AIExecution")
    assert caught.value.status_code == 502
    assert caught.value.detail["code"] == "AI_PROVIDER_FAILURE"
    assert execution.output_contract_version == "v1"
    assert execution.status == "FAILED"
    assert execution.error_code == "AI_PROVIDER_FAILURE"
    assert execution.request_id == "failed-contract-version-test"
    assert db.commit_count == 2
