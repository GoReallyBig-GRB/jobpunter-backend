import pytest
from fastapi import HTTPException

from app.api.v1.admin import _reject_secrets, _validate_config_payload


@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": "do-not-store"},
        {"nested": {"client_secret": "do-not-store"}},
        {"providers": [{"refresh_token": "do-not-store"}]},
        {"private_key": "do-not-store"},
        {"authorization": "Bearer do-not-store"},
        {"model": {"accessToken": "do-not-store"}},
    ],
)
def test_governance_payload_rejects_secret_like_fields_recursively(payload):
    with pytest.raises(HTTPException) as exc:
        _reject_secrets(payload)

    assert exc.value.status_code == 422
    assert "Secrets and credentials" in exc.value.detail


@pytest.mark.parametrize(
    "payload",
    [
        {"unsupported_option": True},
        {"model": "openai-gpt"},
        {"temperature": True},
        {"temperature": 1.1},
        {"system_instruction": "   "},
        {"system_instruction": "x" * 12001},
    ],
)
def test_ai_task_config_rejects_invalid_fields_and_values(payload):
    with pytest.raises(HTTPException) as exc:
        _validate_config_payload("ai.task.cv_generation", payload)

    assert exc.value.status_code == 422


def test_ai_task_config_accepts_supported_fields():
    _validate_config_payload(
        "ai.task.cv_generation",
        {
            "system_instruction": "Extract relevant candidate evidence.",
            "model": "gemini-2.5-flash",
            "temperature": 0.2,
        },
    )


def test_non_ai_system_config_does_not_use_ai_task_schema():
    _validate_config_payload("system.notifications", {"enabled": True})

@pytest.mark.parametrize(
    "config_key",
    ["ai.task.unknown_task", "ai.task.cv_generation.extra", "ai.task."],
)
def test_ai_task_config_rejects_unregistered_task_ids(config_key):
    with pytest.raises(HTTPException) as exc:
        _validate_config_payload(config_key, {"model": "gemini-2.5-flash"})

    assert exc.value.status_code == 422
    assert exc.value.detail == "Unknown AI task configuration key."


def test_ai_task_config_accepts_openai_provider_and_model():
    _validate_config_payload(
        "ai.task.evidence_matching",
        {"provider": "openai", "model": "gpt-4.1-mini", "temperature": 0.1},
    )


def test_ai_task_config_rejects_model_that_conflicts_with_provider():
    with pytest.raises(HTTPException):
        _validate_config_payload(
            "ai.task.evidence_matching",
            {"provider": "google", "model": "gpt-4.1-mini"},
        )
