from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.v1 import admin
from app.ports.identity import AuthenticatedPrincipal


class FakeDB:
    def __init__(self, target):
        self.target = target
        self.added = []
        self.commit_calls = 0
        self.rollback_calls = 0

    def scalar(self, _statement):
        return self.target

    def add(self, item):
        self.added.append(item)

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1


def make_user(**overrides):
    values = {
        "id": uuid4(),
        "status": "ACTIVE",
        "revision": 1,
        "display_name": "Candidate",
        "email": "candidate@example.com",
        "email_verified": True,
        "photo_url": "https://example.com/photo.png",
        "onboarding_status": "IN_PROGRESS",
        "last_login_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def call_update(monkeypatch, target, *, target_status="SUSPENDED", expected_revision=1, reason="Support investigation"):
    monkeypatch.setattr(admin, "_require_admin", lambda _db, _principal: None)
    db = FakeDB(target)
    request = SimpleNamespace(headers={"x-request-id": "request-123"})
    principal = AuthenticatedPrincipal(user_id=uuid4(), auth_provider="firebase")
    payload = admin.UserStatusRequest(
        target_status=target_status,
        expected_revision=expected_revision,
        reason=reason,
    )
    response = admin.update_user_status(
        user_id=target.id,
        payload=payload,
        request=request,
        principal=principal,
        db=db,
    )
    return response, db, principal


def test_account_status_transition_is_revision_checked_and_audited(monkeypatch):
    user = make_user()

    response, db, principal = call_update(monkeypatch, user)

    assert response["status"] == "SUSPENDED"
    assert response["revision"] == 2
    assert response["updated_at"].tzinfo is not None
    assert user.status == "SUSPENDED"
    assert user.revision == 2
    assert db.commit_calls == 1
    assert db.rollback_calls == 0
    assert len(db.added) == 1
    event = db.added[0]
    assert event.actor_user_id == principal.user_id
    assert event.action == "USER_STATUS_CHANGED"
    assert event.resource_type == "user"
    assert event.resource_id == user.id
    assert event.outcome == "SUCCESS"
    assert event.request_id == "request-123"
    assert event.details["previous_status"] == "ACTIVE"
    assert event.details["new_status"] == "SUSPENDED"
    assert event.details["reason"] == "Support investigation"
    assert event.details["revision"] == 2


def test_account_status_transition_rejects_stale_revision_without_mutation(monkeypatch):
    user = make_user(revision=4)
    monkeypatch.setattr(admin, "_require_admin", lambda _db, _principal: None)
    db = FakeDB(user)
    request = SimpleNamespace(headers={})
    payload = admin.UserStatusRequest(
        target_status="DEACTIVATED",
        expected_revision=3,
        reason="Confirmed account closure",
    )

    with pytest.raises(HTTPException) as exc:
        admin.update_user_status(
            user_id=user.id,
            payload=payload,
            request=request,
            principal=AuthenticatedPrincipal(user_id=uuid4()),
            db=db,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "STALE_REVISION"
    assert user.status == "ACTIVE"
    assert user.revision == 4
    assert db.added == []
    assert db.commit_calls == 0


def test_account_status_transition_rejects_no_op_without_audit(monkeypatch):
    user = make_user()
    monkeypatch.setattr(admin, "_require_admin", lambda _db, _principal: None)
    db = FakeDB(user)
    request = SimpleNamespace(headers={})
    payload = admin.UserStatusRequest(
        target_status="ACTIVE",
        expected_revision=1,
        reason="Repeated request",
    )

    with pytest.raises(HTTPException) as exc:
        admin.update_user_status(
            user_id=user.id,
            payload=payload,
            request=request,
            principal=AuthenticatedPrincipal(user_id=uuid4()),
            db=db,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "NO_STATUS_CHANGE"
    assert user.status == "ACTIVE"
    assert user.revision == 1
    assert db.added == []
    assert db.commit_calls == 0


def test_account_status_request_rejects_blank_reason():
    with pytest.raises(ValueError, match="nonblank reason"):
        admin.UserStatusRequest(
            target_status="SUSPENDED",
            expected_revision=1,
            reason="   ",
        )
