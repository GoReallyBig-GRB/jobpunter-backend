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
