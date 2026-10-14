import asyncio
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.application.ai.schemas import GenerateRequest
from app.application.ai.service import execute_ai_task
from app.application.ai.input_contracts import validate_task_input
from app.infrastructure.ai.google_generation import (
    AIProviderFailure,
    AIProviderNotConfigured,
    InvalidStructuredOutput,
    ProviderResult,
)


def _cv_prompt(instruction: str) -> str:
    return json.dumps({
        "candidate_profile": {"summary": "Product delivery leader"},
        "approved_candidate_evidence": [{
            "id": "evidence-1",
            "type": "experience",
            "content": {"summary": "Led technical product delivery."},
            "revision": 1,
        }],
        "target_role": "Technical Product Manager",
        "instruction": instruction,
    })


class FakeSession:
    def __init__(self, config_row):
        self.config_row = config_row
        self.added = []
        self.commit_count = 0

    def scalar(self, _statement):
        return self.config_row

    def add(self, row):
        self.added.append(row)

    def commit(self):
        self.commit_count += 1

    def refresh(self, _row):
        return None


def test_published_task_config_controls_provider_call_and_execution_trace(monkeypatch):
    config_id = uuid4()
    config_row = SimpleNamespace(
        id=config_id,
        payload={
            "model": "gemini-configured-model",
            "system_instruction": "Follow the published task-specific procedure.",
            "temperature": 0.35,
        },
    )
    db = FakeSession(config_row)
    provider_calls = []
    provider_result = ProviderResult(
        raw_text='{"content": "Supported CV content."}',
        parsed_data={"content": "Supported CV content."},
        model_used="gemini-configured-model",
        provider_used="google",
        latency_ms=42,
    )

    async def fake_generate(**kwargs):
        provider_calls.append(kwargs)
        return provider_result

    monkeypatch.setattr("app.application.ai.service.get_ai_provider", lambda _provider: type("FakeAdapter", (), {"generate": staticmethod(fake_generate)})())
    request = GenerateRequest.model_validate({
        "task": "cv_generation",
        "prompt": _cv_prompt("Use the supplied approved candidate evidence."),
        "schemaDescription": "Use the server-owned CV output contract.",
        "options": {"jsonMode": True, "maxTokens": 256},
    })

    response = asyncio.run(execute_ai_task(
        request, db=db, user_id=uuid4(), request_id="ai-config-trace",
    ))

    assert len(provider_calls) == 1
    call = provider_calls[0]
    assert call["task"] == "cv_generation"
    assert call["model_override"] == "gemini-configured-model"
    assert call["task_instruction_override"] == "Follow the published task-specific procedure."
    assert call["temperature_override"] == 0.35
    assert call["json_mode"] is True
    assert call["max_tokens"] == 256

    assert len(db.added) == 1
    execution = db.added[0]
    assert execution.config_version_id == config_id
    assert execution.input_contract_version == "v4"
    assert execution.contract_snapshot["task"] == "cv_generation"
    assert execution.contract_snapshot["input_schema"]["type"] == "object"
    assert execution.contract_snapshot["output_schema"]["type"] == "object"
    assert execution.contract_snapshot["task_instruction"]
    assert execution.contract_snapshot["system_policy"]
    assert execution.contract_snapshot["max_tokens"] == 256
    assert request.prompt not in json.dumps(execution.contract_snapshot, ensure_ascii=False)
    expected_input_fingerprint = hashlib.sha256(
        json.dumps(validate_task_input(request.task, request.prompt), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        .encode("utf-8")
    ).hexdigest()
    assert execution.input_fingerprint == expected_input_fingerprint
    assert len(execution.input_fingerprint) == 64
    assert request.prompt not in execution.input_fingerprint
    assert execution.output_contract_version == "v1"
    assert execution.task == "cv_generation"
    assert execution.request_id == "ai-config-trace"
    assert execution.status == "SUCCEEDED"
    assert execution.validation_result == {"status": "PASSED", "contract_version": "v1"}
    assert execution.model == "gemini-configured-model"
    assert execution.provider == "google"
    assert execution.latency_ms == 42
    assert execution.finished_at is not None
    assert db.commit_count == 2
    assert response.parsed_data == {"content": "Supported CV content."}
    assert response.model_used == "gemini-configured-model"


def test_invalid_published_task_config_is_rejected_before_provider_execution(monkeypatch):
    db = FakeSession(SimpleNamespace(
        id=uuid4(),
        payload={"model": "gemini-2.5-flash", "api_key": "must-not-be-stored"},
    ))
    provider_called = {"value": False}

    async def fake_generate(**_kwargs):
        provider_called["value"] = True
        return ProviderResult("{}", {}, "gemini-2.5-flash", "google", 1)

    monkeypatch.setattr("app.application.ai.service.get_ai_provider", lambda _provider: type("FakeAdapter", (), {"generate": staticmethod(fake_generate)})())
    request = GenerateRequest(task="cv_generation", prompt=_cv_prompt("Generate a CV."))

    with pytest.raises(HTTPException) as error:
        asyncio.run(execute_ai_task(
            request, db=db, user_id=uuid4(), request_id="invalid-config",
        ))

    assert error.value.status_code == 503
    assert error.value.detail["code"] == "AI_CONFIG_INVALID"
    assert provider_called["value"] is False
    assert db.added == []
    assert db.commit_count == 0


def test_candidate_request_cannot_override_provider_or_model():
    with pytest.raises(ValidationError):
        GenerateRequest.model_validate({
            "task": "cv_generation",
            "prompt": "Generate a CV.",
            "provider": "other-provider",
            "model": "candidate-selected-model",
        })



@pytest.mark.parametrize(
    ("provider_error", "expected_status", "expected_code"),
    [
        pytest.param(
            InvalidStructuredOutput("invalid JSON"),
            502,
            "AI_INVALID_JSON",
            id="invalid-structured-output",
        ),
        pytest.param(
            AIProviderFailure("provider failed"),
            502,
            "AI_PROVIDER_FAILURE",
            id="provider-failure",
        ),
        pytest.param(
            AIProviderNotConfigured("not configured"),
            503,
            "AI_NOT_CONFIGURED",
            id="provider-not-configured",
        ),
    ],
)
def test_ai_execution_failure_is_persisted_with_safe_error_category(
    monkeypatch, provider_error, expected_status, expected_code
):
    db = FakeSession(None)

    async def fake_generate(**_kwargs):
        raise provider_error

    monkeypatch.setattr("app.application.ai.service.get_ai_provider", lambda _provider: type("FakeAdapter", (), {"generate": staticmethod(fake_generate)})())
    request = GenerateRequest(task="cv_generation", prompt=_cv_prompt("Generate from approved evidence."))

    with pytest.raises(HTTPException) as error:
        asyncio.run(execute_ai_task(
            request, db=db, user_id=uuid4(), request_id="ai-failure-trace",
        ))

    assert error.value.status_code == expected_status
    assert error.value.detail["code"] == expected_code
    assert len(db.added) == 1
    execution = db.added[0]
    assert execution.status == "FAILED"
    assert execution.error_code == expected_code
    if expected_code == "AI_INVALID_JSON":
        assert execution.validation_result == {"status": "REJECTED", "category": "INVALID_STRUCTURED_OUTPUT"}
    else:
        assert execution.validation_result["status"] == "NOT_RUN"
    assert execution.request_id == "ai-failure-trace"
    assert execution.finished_at is not None
    assert db.commit_count == 2


def test_execution_fingerprint_changes_when_shared_system_policy_changes(monkeypatch):
    dbs = []
    async def fake_generate(**_kwargs):
        return ProviderResult(
            raw_text='{"content": "Supported CV content."}',
            parsed_data={"content": "Supported CV content."},
            model_used="gemini-2.5-flash",
            provider_used="google",
            latency_ms=1,
        )

    monkeypatch.setattr("app.application.ai.service.get_ai_provider", lambda _provider: type("FakeAdapter", (), {"generate": staticmethod(fake_generate)})())
    request = GenerateRequest(task="cv_generation", prompt=_cv_prompt("Use approved evidence only."))
    import app.application.ai.service as ai_service

    monkeypatch.setattr(ai_service, "SYSTEM_POLICY", "shared policy version one")
    first_db = FakeSession(None)
    asyncio.run(execute_ai_task(request, db=first_db, user_id=uuid4()))
    monkeypatch.setattr(ai_service, "SYSTEM_POLICY", "shared policy version two")
    second_db = FakeSession(None)
    asyncio.run(execute_ai_task(request, db=second_db, user_id=uuid4()))

    assert first_db.added[0].config_fingerprint != second_db.added[0].config_fingerprint


def test_ai_execution_uses_each_current_published_config_version(monkeypatch):
    config_v1 = SimpleNamespace(
        id=uuid4(),
        payload={
            "model": "gemini-config-version-one",
            "system_instruction": "Use approved evidence version one.",
            "temperature": 0.2,
        },
    )
    config_v2 = SimpleNamespace(
        id=uuid4(),
        payload={
            "model": "gemini-config-version-two",
            "system_instruction": "Use approved evidence version two.",
            "temperature": 0.4,
        },
    )
    calls = []

    async def fake_generate(**kwargs):
        calls.append(kwargs)
        model = kwargs["model_override"]
        return ProviderResult(
            raw_text='{"content": "Supported CV content."}',
            parsed_data={"content": "Supported CV content."},
            model_used=model,
            provider_used="google",
            latency_ms=10,
        )

    monkeypatch.setattr("app.application.ai.service.get_ai_provider", lambda _provider: type("FakeAdapter", (), {"generate": staticmethod(fake_generate)})())
    request = GenerateRequest(task="cv_generation", prompt=_cv_prompt("Generate from approved evidence."))
    user_id = uuid4()

    first_db = FakeSession(config_v1)
    first = asyncio.run(execute_ai_task(
        request, db=first_db, user_id=user_id, request_id="config-version-one",
    ))
    second_db = FakeSession(config_v2)
    second = asyncio.run(execute_ai_task(
        request, db=second_db, user_id=user_id, request_id="config-version-two",
    ))

    assert [call["model_override"] for call in calls] == [
        "gemini-config-version-one", "gemini-config-version-two",
    ]
    assert [call["task_instruction_override"] for call in calls] == [
        "Use approved evidence version one.", "Use approved evidence version two.",
    ]
    assert first_db.added[0].config_version_id == config_v1.id
    assert second_db.added[0].config_version_id == config_v2.id
    assert first.model_used == "gemini-config-version-one"
    assert second.model_used == "gemini-config-version-two"
    assert first_db.added[0].request_id == "config-version-one"
    assert second_db.added[0].request_id == "config-version-two"


def test_configured_fallback_is_used_once_and_recorded_in_execution_trace(monkeypatch):
    config_id = uuid4()
    db = FakeSession(SimpleNamespace(
        id=config_id,
        payload={
            "provider": "google",
            "model": "gemini-primary",
            "fallback_provider": "openai",
            "fallback_model": "gpt-fallback",
            "temperature": 0.2,
        },
    ))
    calls = []

    async def primary_failure(**kwargs):
        calls.append(("google", kwargs))
        raise AIProviderFailure("primary provider unavailable")

    async def fallback_success(**kwargs):
        calls.append(("openai", kwargs))
        return ProviderResult(
            raw_text='{"content": "Supported CV content."}',
            parsed_data={"content": "Supported CV content."},
            model_used="gpt-fallback",
            provider_used="openai",
            latency_ms=19,
        )

    adapters = {
        "google": type("PrimaryAdapter", (), {"generate": staticmethod(primary_failure)})(),
        "openai": type("FallbackAdapter", (), {"generate": staticmethod(fallback_success)})(),
    }
    monkeypatch.setattr(
        "app.application.ai.service.get_ai_provider",
        lambda name: adapters[name],
    )
    response = asyncio.run(execute_ai_task(
        GenerateRequest(task="cv_generation", prompt=_cv_prompt("Use approved candidate evidence.")),
        db=db,
        user_id=uuid4(),
        request_id="fallback-trace",
    ))

    assert [provider for provider, _ in calls] == ["google", "openai"]
    assert calls[1][1]["model_override"] == "gpt-fallback"
    execution = db.added[0]
    assert execution.config_version_id == config_id
    assert execution.status == "SUCCEEDED"
    assert execution.fallback_attempted is True
    assert execution.fallback_used is True
    assert execution.provider == "openai"
    assert execution.model == "gpt-fallback"
    assert response.is_fallback is True
    assert response.provider_used == "openai"


def test_configured_fallback_failure_is_reported_and_traced(monkeypatch):
    db = FakeSession(SimpleNamespace(
        id=uuid4(),
        payload={
            "provider": "google",
            "model": "gemini-primary",
            "fallback_provider": "openai",
            "fallback_model": "gpt-fallback",
        },
    ))

    async def fail(**_kwargs):
        raise AIProviderFailure("provider unavailable")

    monkeypatch.setattr(
        "app.application.ai.service.get_ai_provider",
        lambda _name: type("FailingAdapter", (), {"generate": staticmethod(fail)})(),
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(execute_ai_task(
            GenerateRequest(task="cv_generation", prompt=_cv_prompt("Use approved candidate evidence.")),
            db=db,
            user_id=uuid4(),
            request_id="fallback-failure",
        ))

    assert error.value.status_code == 502
    assert error.value.detail["code"] == "AI_PROVIDER_FAILURE"
    execution = db.added[0]
    assert execution.status == "FAILED"
    assert execution.error_code == "AI_PROVIDER_FAILURE"
    assert execution.fallback_attempted is True
    assert execution.fallback_used is False


def test_tampered_published_config_fingerprint_fails_closed(monkeypatch):
    db = FakeSession(SimpleNamespace(
        id=uuid4(),
        payload={"provider": "google", "temperature": 0.2},
        payload_fingerprint="0" * 64,
    ))
    provider_called = {"value": False}

    async def fake_generate(**_kwargs):
        provider_called["value"] = True
        return ProviderResult(
            raw_text='{"content": "Supported CV content."}',
            parsed_data={"content": "Supported CV content."},
            model_used="gemini-2.5-flash",
            provider_used="google",
            latency_ms=1,
        )

    monkeypatch.setattr(
        "app.application.ai.service.get_ai_provider",
        lambda _name: type("FakeAdapter", (), {"generate": staticmethod(fake_generate)})(),
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(execute_ai_task(
            GenerateRequest(task="cv_generation", prompt=_cv_prompt("Use approved evidence.")),
            db=db,
            user_id=uuid4(),
        ))

    assert error.value.status_code == 503
    assert error.value.detail["code"] == "AI_CONFIG_INVALID"
    assert provider_called["value"] is False
    assert db.added == []


def test_fallback_output_must_pass_the_same_server_owned_contract(monkeypatch):
    db = FakeSession(SimpleNamespace(
        id=uuid4(),
        payload={
            "provider": "google",
            "model": "gemini-primary",
            "fallback_provider": "openai",
            "fallback_model": "gpt-fallback",
        },
    ))

    async def primary_failure(**_kwargs):
        raise AIProviderFailure("primary unavailable")

    async def invalid_fallback(**_kwargs):
        return ProviderResult(
            raw_text='{"wrong_field": "not a valid CV output"}',
            parsed_data={"wrong_field": "not a valid CV output"},
            model_used="gpt-fallback",
            provider_used="openai",
            latency_ms=12,
        )

    adapters = {
        "google": type("PrimaryAdapter", (), {"generate": staticmethod(primary_failure)})(),
        "openai": type("FallbackAdapter", (), {"generate": staticmethod(invalid_fallback)})(),
    }
    monkeypatch.setattr(
        "app.application.ai.service.get_ai_provider",
        lambda name: adapters[name],
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(execute_ai_task(
            GenerateRequest(task="cv_generation", prompt=_cv_prompt("Use approved candidate evidence.")),
            db=db,
            user_id=uuid4(),
            request_id="fallback-invalid-output",
        ))

    assert error.value.status_code == 502
    assert error.value.detail["code"] == "AI_INVALID_OUTPUT"
    execution = db.added[0]
    assert execution.status == "FAILED"
    assert execution.fallback_attempted is True
    assert execution.fallback_used is True
    assert execution.error_code == "AI_INVALID_OUTPUT"
    assert execution.validation_result == {
        "status": "REJECTED",
        "category": "OUTPUT_CONTRACT_INVALID",
        "contract_version": "v1",
    }
