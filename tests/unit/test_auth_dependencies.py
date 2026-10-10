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

@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/v1/candidate/evidence",
            {
                "evidence_type": "resume",
                "content": {"text": "candidate-provided evidence"},
                "source_type": "manual",
                "source_reference": "candidate-upload",
            },
        ),
        ("/api/v1/candidate/evidence/00000000-0000-0000-0000-000000000001/submit", {"expected_revision": 1}),
        ("/api/v1/candidate/evidence/00000000-0000-0000-0000-000000000001/approve", {"expected_revision": 1}),
        ("/api/v1/candidate/evidence/00000000-0000-0000-0000-000000000001/reject", {"expected_revision": 1}),
        ("/api/v1/candidate/evidence/00000000-0000-0000-0000-000000000001/archive", {"expected_revision": 1}),
    ],
)
def test_candidate_evidence_mutations_require_authenticated_identity(monkeypatch, path, payload):
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
        response = client.post(path, json=payload, headers={"X-User-ID": str(uuid4())})
    finally:
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing authentication token"


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/v1/candidate/claims",
            {
                "claim_type": "experience",
                "claim_content": {"summary": "candidate-provided claim"},
                "evidence_ids": [],
            },
        ),
        ("/api/v1/candidate/claims/00000000-0000-0000-0000-000000000001/submit", {"expected_revision": 1}),
        ("/api/v1/candidate/claims/00000000-0000-0000-0000-000000000001/approve", {"expected_revision": 1}),
        ("/api/v1/candidate/claims/00000000-0000-0000-0000-000000000001/reject", {"expected_revision": 1}),
        ("/api/v1/candidate/claims/00000000-0000-0000-0000-000000000001/archive", {"expected_revision": 1}),
    ],
)
def test_candidate_claim_mutations_require_authenticated_identity(monkeypatch, path, payload):
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
        response = client.post(path, json=payload, headers={"X-User-ID": str(uuid4())})
    finally:
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing authentication token"


def test_candidate_preferences_update_requires_authenticated_identity(monkeypatch):
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
        response = client.put(
            "/api/v1/candidate/preferences",
            json={"expectedRevision": 0, "preferences": {"targetRoles": ["Product Manager"]}},
            headers={"X-User-ID": str(uuid4())},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing authentication token"


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/v1/candidate/evaluations",
            {"job_id": "00000000-0000-0000-0000-000000000002", "target_role_key": "technical-product-manager"},
        ),
        ("/api/v1/candidate/punts", {"evaluation_id": "00000000-0000-0000-0000-000000000003"}),
        ("/api/v1/candidate/applications", {"punt_id": "00000000-0000-0000-0000-000000000004"}),
        (
            "/api/v1/candidate/applications/00000000-0000-0000-0000-000000000005/transition",
            {"target_status": "SUBMITTED", "expected_revision": 1},
        ),
    ],
)
def test_candidate_opportunity_mutations_require_authenticated_identity(monkeypatch, path, payload):
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
        response = client.post(path, json=payload, headers={"X-User-ID": str(uuid4())})
    finally:
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing authentication token"


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "POST",
            "/api/v1/candidate/applications/00000000-0000-0000-0000-000000000010/packages",
            {"items": [{"artifact_type": "cover_letter", "content": {"content": "Draft cover letter"}}]},
        ),
        (
            "GET",
            "/api/v1/candidate/applications/00000000-0000-0000-0000-000000000010/packages",
            None,
        ),
        (
            "GET",
            "/api/v1/candidate/applications/00000000-0000-0000-0000-000000000010/packages/00000000-0000-0000-0000-000000000011",
            None,
        ),
        (
            "POST",
            "/api/v1/candidate/applications/00000000-0000-0000-0000-000000000010/packages/00000000-0000-0000-0000-000000000011/validate-claims",
            None,
        ),
        (
            "POST",
            "/api/v1/candidate/applications/00000000-0000-0000-0000-000000000010/packages/00000000-0000-0000-0000-000000000011/prepare-review",
            None,
        ),
        (
            "POST",
            "/api/v1/candidate/applications/00000000-0000-0000-0000-000000000010/packages/00000000-0000-0000-0000-000000000011/review-decision",
            {"decision": "approve"},
        ),
    ],
)
def test_application_package_routes_require_authenticated_identity(monkeypatch, method, path, payload):
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
        headers = {"X-User-ID": str(uuid4())}
        if payload is None:
            response = client.request(method, path, headers=headers)
        else:
            response = client.request(method, path, json=payload, headers=headers)
    finally:
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing authentication token"


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/api/v1/admin/audit", None),
        ("GET", "/api/v1/admin/configs", None),
        ("GET", "/api/v1/admin/grants", None),
        (
            "POST",
            "/api/v1/admin/configs",
            {"config_key": "system.example", "payload": {"enabled": True}},
        ),
        (
            "POST",
            "/api/v1/admin/grants",
            {"user_id": "00000000-0000-0000-0000-000000000020", "role": "ADMIN"},
        ),
        (
            "POST",
            "/api/v1/admin/sources",
            {"name": "Test source", "board_token": "valid-board-token"},
        ),
        (
            "PATCH",
            "/api/v1/admin/sources/00000000-0000-0000-0000-000000000021/status",
            {"status": "PAUSED"},
        ),
    ],
)
def test_admin_governance_routes_require_authenticated_identity(monkeypatch, method, path, payload):
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
        headers = {"X-User-ID": str(uuid4())}
        if payload is None:
            response = client.request(method, path, headers=headers)
        else:
            response = client.request(method, path, json=payload, headers=headers)
    finally:
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing authentication token"


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "POST",
            "/api/v1/admin/configs/00000000-0000-0000-0000-000000000030/publish",
            None,
        ),
        (
            "POST",
            "/api/v1/admin/configs/00000000-0000-0000-0000-000000000030/rollback",
            None,
        ),
        (
            "POST",
            "/api/v1/admin/grants/00000000-0000-0000-0000-000000000031/revoke",
            None,
        ),
        (
            "POST",
            "/api/v1/admin/sources/00000000-0000-0000-0000-000000000032/connectors/00000000-0000-0000-0000-000000000033/health-check",
            None,
        ),
        (
            "POST",
            "/api/v1/admin/sources/00000000-0000-0000-0000-000000000032/acquisition-runs",
            {
                "connector_id": "00000000-0000-0000-0000-000000000033",
                "run_key": "auth-test-run-1",
            },
        ),
    ],
)
def test_admin_governance_mutations_require_authenticated_identity(monkeypatch, method, path, payload):
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
        headers = {"X-User-ID": str(uuid4())}
        if payload is None:
            response = client.request(method, path, headers=headers)
        else:
            response = client.request(method, path, json=payload, headers=headers)
    finally:
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing authentication token"


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/v1/ai/generate",
            {"task": "cover_letter", "prompt": "Draft a cover letter from the supplied evidence."},
        ),
        (
            "/api/v1/email/parse-job-alert",
            {
                "subject": "Technical Product Manager role",
                "content": "A sample job alert message with role and employer details.",
                "sender": "alerts@example.com",
            },
        ),
    ],
)
def test_ai_and_email_processing_routes_require_authenticated_identity(monkeypatch, path, payload):
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
        response = client.post(path, json=payload, headers={"X-User-ID": str(uuid4())})
    finally:
        app.dependency_overrides.pop(get_db, None)
        client.close()

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing authentication token"
