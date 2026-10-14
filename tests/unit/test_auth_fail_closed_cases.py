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
