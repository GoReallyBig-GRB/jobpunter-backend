import uuid

import pytest
from fastapi import HTTPException

from app.api.v1.requirement_review import _require_reviewer
from app.ports.identity import AuthenticatedPrincipal


def test_review_routes_fail_closed_outside_explicit_development(monkeypatch):
    monkeypatch.setenv("JOBPUNTER_REQUIREMENT_REVIEWER_USER_IDS", str(uuid.uuid4()))
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "production")
    monkeypatch.setenv("JOBPUNTER_ALLOW_DEV_IDENTITY", "true")

    with pytest.raises(HTTPException) as error:
        _require_reviewer(AuthenticatedPrincipal(user_id=uuid.uuid4()))

    assert error.value.status_code == 503


def test_review_routes_fail_closed_without_dev_identity_opt_in(monkeypatch):
    user_id = uuid.uuid4()
    monkeypatch.setenv("JOBPUNTER_REQUIREMENT_REVIEWER_USER_IDS", str(user_id))
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "development")
    monkeypatch.delenv("JOBPUNTER_ALLOW_DEV_IDENTITY", raising=False)

    with pytest.raises(HTTPException) as error:
        _require_reviewer(AuthenticatedPrincipal(user_id=user_id))

    assert error.value.status_code == 503


def test_review_routes_allow_configured_reviewer_only_in_explicit_dev_mode(monkeypatch):
    user_id = uuid.uuid4()
    monkeypatch.setenv("JOBPUNTER_REQUIREMENT_REVIEWER_USER_IDS", str(user_id))
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "development")
    monkeypatch.setenv("JOBPUNTER_ALLOW_DEV_IDENTITY", "true")

    _require_reviewer(AuthenticatedPrincipal(user_id=user_id))


def test_review_routes_reject_non_allowlisted_dev_user(monkeypatch):
    monkeypatch.setenv("JOBPUNTER_REQUIREMENT_REVIEWER_USER_IDS", str(uuid.uuid4()))
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "development")
    monkeypatch.setenv("JOBPUNTER_ALLOW_DEV_IDENTITY", "true")

    with pytest.raises(HTTPException) as error:
        _require_reviewer(AuthenticatedPrincipal(user_id=uuid.uuid4()))

    assert error.value.status_code == 403


def test_firebase_provider_module_is_importable():
    from app.infrastructure.identity.firebase import FirebaseIdentityProvider

    provider = FirebaseIdentityProvider(token_verifier=lambda _token: {"uid": "test-user"})
    assert provider is not None


def test_firebase_provider_rejects_missing_bearer_header():
    from app.infrastructure.identity.firebase import FirebaseIdentityProvider
    import pytest

    provider = FirebaseIdentityProvider(token_verifier=lambda _token: {"uid": "test-user"})
    with pytest.raises(ValueError, match="Bearer token required"):
        provider.authenticate(None, None)


def test_firebase_provider_rejects_token_without_uid():
    from app.infrastructure.identity.firebase import FirebaseIdentityProvider
    import pytest

    provider = FirebaseIdentityProvider(token_verifier=lambda _token: {"name": "Candidate"})
    with pytest.raises(ValueError, match="UID"):
        provider.authenticate("Bearer test-token", None)


def test_firebase_verification_rejects_mismatched_project(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    from app.infrastructure.identity.firebase import FirebaseIdentityProvider

    app = SimpleNamespace(project_id="wrong-project")
    admin_module = ModuleType("firebase_admin")
    admin_module.get_app = lambda: app
    admin_module.auth = ModuleType("firebase_admin.auth")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "expected-project")
    monkeypatch.setitem(sys.modules, "firebase_admin", admin_module)
    monkeypatch.setitem(sys.modules, "firebase_admin.auth", admin_module.auth)

    with pytest.raises(RuntimeError, match="does not match FIREBASE_PROJECT_ID"):
        FirebaseIdentityProvider._verify_with_firebase("test-token")


def test_firebase_verification_checks_revocation_for_configured_project(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace
    from app.infrastructure.identity.firebase import FirebaseIdentityProvider

    app = SimpleNamespace(project_id="expected-project")
    calls = []
    auth_module = ModuleType("firebase_admin.auth")
    auth_module.verify_id_token = lambda token, app, check_revoked: (
        calls.append((token, app, check_revoked)) or {"uid": "firebase-user"}
    )
    admin_module = ModuleType("firebase_admin")
    admin_module.get_app = lambda: app
    admin_module.auth = auth_module
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "expected-project")
    monkeypatch.setitem(sys.modules, "firebase_admin", admin_module)
    monkeypatch.setitem(sys.modules, "firebase_admin.auth", auth_module)

    claims = FirebaseIdentityProvider._verify_with_firebase("test-token")

    assert claims == {"uid": "firebase-user"}
    assert calls == [("test-token", app, True)]
