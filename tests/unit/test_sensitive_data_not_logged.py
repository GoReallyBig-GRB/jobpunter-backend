import logging
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import api as api_package
from app.api import deps


def test_rejected_bearer_secret_is_not_written_to_application_logs(monkeypatch, caplog):
    secret = "SYNTHETIC_BEARER_SECRET_93c8d1"
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "production")
    monkeypatch.delenv("JOBPUNTER_ALLOW_DEV_IDENTITY", raising=False)

    class RejectingProvider:
        def authenticate(self, authorization, _db):
            raise ValueError(f"provider rejection included credential {secret}")

    monkeypatch.setattr(deps, "FirebaseIdentityProvider", RejectingProvider)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(HTTPException) as raised:
        deps.get_principal(
            authorization=f"Bearer {secret}",
            x_user_id=str(uuid4()),
            db=object(),
        )

    assert raised.value.status_code == 401
    assert raised.value.detail == "Invalid or missing authentication token"
    assert secret not in caplog.text
