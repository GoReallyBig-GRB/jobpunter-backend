from uuid import uuid4
import pytest
from fastapi import HTTPException
from app.api.v1.admin import ConfigDraftRequest, _reject_secrets, _require_admin
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
    assert ConfigDraftRequest(config_key="system.discovery", payload={"enabled": True})
    with pytest.raises(ValueError):
        ConfigDraftRequest(config_key="user.profile", payload={"x": 1})
    with pytest.raises(ValueError):
        ConfigDraftRequest(config_key="ai.task.Cover_Letter", payload={"enabled": True})
