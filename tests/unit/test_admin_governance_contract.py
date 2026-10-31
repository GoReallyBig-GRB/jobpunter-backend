from uuid import uuid4
import pytest
from fastapi import HTTPException
from app.api.v1.admin import ConfigDraftRequest, SMTPProviderDraftRequest, _reject_secrets, _require_admin, _validate_config_payload
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


def test_ai_task_fallback_requires_explicit_distinct_provider_and_matching_model():
    _validate_config_payload("ai.task.cover_letter", {
        "provider": "google",
        "model": "gemini-primary",
        "fallback_provider": "openai",
        "fallback_model": "gpt-fallback",
    })
    with pytest.raises(HTTPException):
        _validate_config_payload("ai.task.cover_letter", {
            "fallback_provider": "openai",
            "fallback_model": "gpt-fallback",
        })
    with pytest.raises(HTTPException):
        _validate_config_payload("ai.task.cover_letter", {
            "provider": "google",
            "fallback_provider": "google",
            "fallback_model": "gemini-fallback",
        })
    with pytest.raises(HTTPException):
        _validate_config_payload("ai.task.cover_letter", {
            "provider": "google",
            "fallback_provider": "openai",
            "fallback_model": "gemini-wrong-provider",
        })
    with pytest.raises(HTTPException):
        _validate_config_payload("ai.task.cover_letter", {
            "provider": "google",
            "fallback_provider": "openai",
        })


def test_admin_access_denial_fails_closed_when_audit_persistence_fails():
    from starlette.requests import Request

    class FailingAuditDB(FakeDB):
        def __init__(self):
            super().__init__(result=None)
            self.added = []
            self.rolled_back = False

        def get(self, model, user_id):
            return object()

        def add(self, event):
            self.added.append(event)

        def commit(self):
            raise RuntimeError("simulated audit store outage")

        def rollback(self):
            self.rolled_back = True

    db = FailingAuditDB()
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/v1/admin/users",
        "headers": [(b"x-request-id", b"audit-failure-test")],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 123),
    })
    with pytest.raises(HTTPException) as exc:
        _require_admin(
            db,
            AuthenticatedPrincipal(user_id=uuid4(), auth_provider="test"),
            request,
        )
    assert exc.value.status_code == 500
    assert "could not be recorded" in exc.value.detail
    assert db.rolled_back is True
    assert len(db.added) == 1
    assert db.added[0].outcome == "DENIED"


def test_admin_smtp_configuration_requires_one_secure_tls_mode():
    with pytest.raises(ValueError):
        SMTPProviderDraftRequest(
            provider_name="Example",
            host="smtp.example.test",
            use_tls=False,
            use_ssl=False,
            from_email="noreply@example.test",
        )
    with pytest.raises(ValueError):
        SMTPProviderDraftRequest(
            provider_name="Example",
            host="smtp.example.test",
            use_tls=True,
            use_ssl=True,
            from_email="noreply@example.test",
        )


def test_admin_smtp_configuration_rejects_unregistered_notification_events():
    with pytest.raises(ValueError):
        SMTPProviderDraftRequest(
            provider_name="Example",
            host="smtp.example.test",
            use_tls=True,
            from_email="noreply@example.test",
            event_policy={"MADE_UP_EVENT": True},
        )
