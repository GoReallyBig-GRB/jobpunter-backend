from uuid import uuid4
import pytest
from fastapi import HTTPException
from app.api.v1.admin import ConfigDraftRequest, _reject_secrets, _require_admin, _validate_config_payload
from app.ports.identity import AuthenticatedPrincipal

class FakeDB:
    def __init__(self, result=None):
        self.result = result
    def scalar(self, _statement):
        return self.result

def test_admin_authorization_fails_closed_without_active_grant():
    with pytest.raises(HTTPException) as exc:
        _require_admin(FakeDB(), AuthenticatedPrincipal(user_id=uuid4(), auth_provider="test"))
    assert exc.value.status_code == 403

def test_admin_authorization_accepts_only_a_grant_returned_by_the_authoritative_query():
    _require_admin(FakeDB(result=object()), AuthenticatedPrincipal(user_id=uuid4(), auth_provider="test"))

@pytest.mark.parametrize("key", ["api_key", "APIKEY", "secret", "password", "access_token", "refresh_token", "credentials"])
def test_governance_payload_rejects_secret_fields_recursively(key):
    with pytest.raises(HTTPException) as exc:
        _reject_secrets({"nested": [{"safe": True, key: "never persist"}]})
    assert exc.value.status_code == 422

def test_governance_config_keys_are_limited_to_supported_namespaces():
    assert ConfigDraftRequest(config_key="ai.task.cover_letter", payload={"temperature": 0.2})
    with pytest.raises(ValueError):
        ConfigDraftRequest(config_key="system.discovery", payload={"enabled": True})
    with pytest.raises(ValueError):
        ConfigDraftRequest(config_key="user.profile", payload={"x": 1})
    with pytest.raises(ValueError):
        ConfigDraftRequest(config_key="ai.task.Cover_Letter", payload={"enabled": True})


def test_ai_task_config_rejects_unknown_fields_and_unapproved_models():
    with pytest.raises(HTTPException):
        _validate_config_payload("ai.task.cover_letter", {"api_endpoint": "https://example.invalid"})
    with pytest.raises(HTTPException):
        _validate_config_payload("ai.task.cover_letter", {"model": "other-provider-model"})


def test_ai_task_config_bounds_temperature_and_instruction():
    with pytest.raises(HTTPException):
        _validate_config_payload("ai.task.cover_letter", {"temperature": 1.5})
    with pytest.raises(HTTPException):
        _validate_config_payload("ai.task.cover_letter", {"system_instruction": " "})
    _validate_config_payload("ai.task.cover_letter", {
        "model": "gemini-2.5-flash",
        "temperature": 0.3,
        "system_instruction": "Keep answers concise.",
    })


def test_ai_execution_task_filter_uses_the_canonical_task_registry():
    from app.api.v1.admin import _validate_ai_task_filter
    from app.application.ai.task_registry import AI_TASK_REGISTRY

    for task_id in AI_TASK_REGISTRY:
        _validate_ai_task_filter(task_id)

    with pytest.raises(HTTPException) as exc:
        _validate_ai_task_filter("not_a_registered_task")
    assert exc.value.status_code == 422
