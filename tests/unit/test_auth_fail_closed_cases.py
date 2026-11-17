from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import deps


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "not-a-bearer-token",
        "Bearer expired-token",
        "Bearer revoked-token",
    ],
    ids=["missing", "empty", "malformed", "expired", "revoked"],
)
def test_production_rejected_credentials_cannot_fall_back_to_user_header(
    monkeypatch, authorization
):
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "production")
    # Even a mistakenly enabled development flag must not enable header identity in production.
    monkeypatch.setenv("JOBPUNTER_ALLOW_DEV_IDENTITY", "true")
    supplied = []

    class RejectingFirebaseProvider:
        def authenticate(self, received_authorization, db):
            supplied.append(received_authorization)
            raise ValueError("credential rejected")

    monkeypatch.setattr(deps, "FirebaseIdentityProvider", RejectingFirebaseProvider)

    with pytest.raises(HTTPException) as exc:
        deps.get_principal(
            authorization=authorization,
            x_user_id=str(uuid4()),
            db=object(),
        )

    assert supplied == [authorization]
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid or missing authentication token"



@pytest.mark.parametrize("configured_provider", [None, "firebase", " FIREBASE "])
def test_identity_provider_selection_is_server_configured_and_normalized(
    monkeypatch, configured_provider
):
    class SelectedFirebaseProvider:
        pass

    if configured_provider is None:
        monkeypatch.delenv("JOBPUNTER_IDENTITY_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("JOBPUNTER_IDENTITY_PROVIDER", configured_provider)
    monkeypatch.setattr(deps, "FirebaseIdentityProvider", SelectedFirebaseProvider)

    assert isinstance(deps.get_identity_provider(), SelectedFirebaseProvider)


def test_unknown_identity_provider_fails_closed_in_production(monkeypatch):
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "production")
    monkeypatch.setenv("JOBPUNTER_ALLOW_DEV_IDENTITY", "true")
    monkeypatch.setenv("JOBPUNTER_IDENTITY_PROVIDER", "unrecognized-provider")

    with pytest.raises(HTTPException) as exc:
        deps.get_principal(
            authorization=None,
            x_user_id=str(uuid4()),
            db=object(),
        )

    assert exc.value.status_code == 503
    assert exc.value.detail == "Authentication service is not configured"


def test_firebase_verification_failure_logs_only_the_exception_class(monkeypatch, caplog):
    import sys
    import types

    from app.infrastructure.identity.firebase import FirebaseIdentityProvider

    class BoomError(Exception):
        pass

    fake_auth = types.SimpleNamespace(
        verify_id_token=lambda token, app=None, check_revoked=False: (_ for _ in ()).throw(
            BoomError("token-secret-value")
        )
    )
    fake_app = types.SimpleNamespace(project_id="proj")
    fake_admin = types.ModuleType("firebase_admin")
    fake_admin.get_app = lambda: fake_app
    fake_admin.auth = fake_auth
    fake_admin_auth_module = types.ModuleType("firebase_admin.auth")
    fake_admin_auth_module.verify_id_token = fake_auth.verify_id_token
    monkeypatch.setitem(sys.modules, "firebase_admin", fake_admin)
    monkeypatch.setitem(sys.modules, "firebase_admin.auth", fake_admin_auth_module)
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "proj")

    with caplog.at_level("WARNING"), pytest.raises(ValueError):
        FirebaseIdentityProvider._verify_with_firebase("token-secret-value")
    assert "reason=BoomError" in caplog.text
    assert "token-secret-value" not in caplog.text


def test_service_account_credential_is_optional_and_fails_closed_without_leaking(monkeypatch):
    from app.infrastructure.identity import firebase as fb

    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    assert fb._service_account_credential() is None

    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", "{not json: private-key-text")
    with pytest.raises(RuntimeError) as excinfo:
        fb._service_account_credential()
    assert "private-key-text" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None  # no chained message that could echo the secret


def test_service_account_json_builds_a_certificate_credential(monkeypatch):
    import json
    import types

    from app.infrastructure.identity import firebase as fb

    seen = {}
    fake_credentials = types.SimpleNamespace(Certificate=lambda data: seen.setdefault("data", data) or "cred")
    import sys

    fake_admin = types.ModuleType("firebase_admin")
    fake_admin.credentials = fake_credentials
    monkeypatch.setitem(sys.modules, "firebase_admin", fake_admin)
    monkeypatch.setitem(sys.modules, "firebase_admin.credentials", fake_credentials)
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", json.dumps({"project_id": "proj", "type": "service_account"}))
    assert fb._service_account_credential() is not None
    assert seen["data"]["project_id"] == "proj"
