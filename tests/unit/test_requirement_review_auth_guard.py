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
