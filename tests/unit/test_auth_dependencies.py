from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import deps
from app.ports.identity import AuthenticatedPrincipal


def test_invalid_firebase_token_maps_to_401(monkeypatch):
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "production")
    monkeypatch.delenv("JOBPUNTER_ALLOW_DEV_IDENTITY", raising=False)

    class InvalidTokenProvider:
        def authenticate(self, authorization, db):
            raise ValueError("bad token")

    monkeypatch.setattr(deps, "FirebaseIdentityProvider", InvalidTokenProvider)

    with pytest.raises(HTTPException) as exc:
        deps.get_principal(authorization="Bearer bad-token", x_user_id=None, db=object())

    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid or missing authentication token"


def test_unconfigured_firebase_maps_to_503(monkeypatch):
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "production")
    monkeypatch.delenv("JOBPUNTER_ALLOW_DEV_IDENTITY", raising=False)

    class UnconfiguredProvider:
        def authenticate(self, authorization, db):
            raise RuntimeError("missing Firebase configuration")

    monkeypatch.setattr(deps, "FirebaseIdentityProvider", UnconfiguredProvider)

    with pytest.raises(HTTPException) as exc:
        deps.get_principal(authorization="Bearer token", x_user_id=None, db=object())

    assert exc.value.status_code == 503
    assert exc.value.detail == "Authentication service is not configured"


def test_development_identity_requires_both_opt_in_flags(monkeypatch):
    user_id = uuid4()
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "development")
    monkeypatch.setenv("JOBPUNTER_ALLOW_DEV_IDENTITY", "true")

    class FirebaseMustNotRun:
        def __init__(self):
            pytest.fail("Firebase should not run for explicitly enabled development identity")

    monkeypatch.setattr(deps, "FirebaseIdentityProvider", FirebaseMustNotRun)

    principal = deps.get_principal(authorization=None, x_user_id=str(user_id), db=object())

    assert principal.user_id == user_id
    assert principal.auth_provider == "development"


@pytest.mark.parametrize(
    ("environment", "allow_dev_identity"),
    [
        ("production", "true"),
        ("test", "true"),
        ("development", "false"),
        ("development", ""),
    ],
)
def test_x_user_id_is_not_trusted_without_both_development_flags(
    monkeypatch, environment, allow_dev_identity
):
    user_id = uuid4()
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", environment)
    monkeypatch.setenv("JOBPUNTER_ALLOW_DEV_IDENTITY", allow_dev_identity)

    expected = AuthenticatedPrincipal(user_id=uuid4(), auth_provider="firebase")

    class FirebaseProvider:
        def authenticate(self, authorization, db):
            assert authorization == "Bearer valid-token"
            return expected

    monkeypatch.setattr(deps, "FirebaseIdentityProvider", FirebaseProvider)

    principal = deps.get_principal(
        authorization="Bearer valid-token",
        x_user_id=str(user_id),
        db=object(),
    )

    assert principal is expected
    assert principal.user_id != user_id
    assert principal.auth_provider == "firebase"


def test_invalid_development_user_id_falls_back_to_firebase(monkeypatch):
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "development")
    monkeypatch.setenv("JOBPUNTER_ALLOW_DEV_IDENTITY", "true")
    expected = AuthenticatedPrincipal(user_id=uuid4(), auth_provider="firebase")

    class FirebaseProvider:
        def authenticate(self, authorization, db):
            return expected

    monkeypatch.setattr(deps, "FirebaseIdentityProvider", FirebaseProvider)

    principal = deps.get_principal(
        authorization=None,
        x_user_id="not-a-uuid",
        db=object(),
    )

    assert principal is expected

@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/candidate/profile",
        "/api/v1/candidate/preferences",
        "/api/v1/candidate/claims",
        "/api/v1/candidate/evaluations",
        "/api/v1/candidate/punts",
        "/api/v1/candidate/applications",
    ],
)
def test_private_candidate_routes_reject_spoofed_user_header_in_production(
    monkeypatch, path
):
    from fastapi.testclient import TestClient

    from app.infrastructure.db.session import get_db
    from app.main import app

    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "production")
    monkeypatch.setenv("JOBPUNTER_ALLOW_DEV_IDENTITY", "true")

    class InvalidTokenProvider:
        def authenticate(self, authorization, db):
            raise ValueError("bad token")

    monkeypatch.setattr(deps, "FirebaseIdentityProvider", InvalidTokenProvider)
    app.dependency_overrides[get_db] = lambda: object()
    client = TestClient(app)
    try:
        response = client.get(path, headers={"X-User-ID": str(uuid4())})
    finally:
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing authentication token"

def test_app_env_alone_cannot_enable_development_identity(monkeypatch):
    user_id = uuid4()
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("JOBPUNTER_ENVIRONMENT", raising=False)
    monkeypatch.setenv("JOBPUNTER_ALLOW_DEV_IDENTITY", "true")
    expected = AuthenticatedPrincipal(user_id=uuid4(), auth_provider="firebase")

    class FirebaseProvider:
        def authenticate(self, authorization, db):
            assert authorization == "Bearer valid-token"
            return expected

    monkeypatch.setattr(deps, "FirebaseIdentityProvider", FirebaseProvider)

    principal = deps.get_principal(
        authorization="Bearer valid-token",
        x_user_id=str(user_id),
        db=object(),
    )

    assert principal is expected
    assert principal.user_id != user_id
    assert principal.auth_provider == "firebase"
