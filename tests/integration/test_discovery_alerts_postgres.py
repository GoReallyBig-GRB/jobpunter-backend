"""E5.5: discovery alerts open once per episode, resolve themselves, email administrators, and are acknowledgeable."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.api.deps import get_principal
from app.application.jobs import discovery_alerts as rules
from app.application.notifications.service import notification_email_delivery_handler
from app.infrastructure.db.models import (
    AcquisitionRun, DiscoveryAlert, DiscoveryMissionSourceRun, Notification,
    NotificationEmailDelivery, OutboxEvent, SourceRefreshSchedule, User,
)
from app.infrastructure.db.models.governance import AdminAuditEvent, AdminGrant
from app.infrastructure.db.session import get_db
from app.main import app
from tests.integration.test_admin_governance_postgres import _admin_test_client, db  # noqa: F401
from tests.integration.test_discovery_missions_postgres import _seed_active_sources
from tests.integration.test_notification_email_delivery_postgres import FakeSMTP, factory, settings

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)
NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def _admin(db, *, verified=True, status="ACTIVE", grant=True):
    admin = User(display_name="alert admin", email=f"{uuid4()}@example.test", email_verified=verified, status=status)
    db.add(admin)
    db.flush()
    if grant:
        db.add(AdminGrant(user_id=admin.id, role="ADMIN", active=True))
        db.flush()
    return admin


def _scope(db, *, created=NOW - timedelta(days=30)):
    user = User(display_name="alert creator")
    db.add(user)
    db.flush()
    source, connector, _t = _seed_active_sources(db, 1, created_by_user_id=user.id)[0]
    source.created_at = created
    schedule = SourceRefreshSchedule(
        source_id=source.id, status="PAUSED", interval_minutes=60, next_run_at=NOW + timedelta(days=1),
        created_by_user_id=user.id, updated_by_user_id=user.id,
    )
    db.add(schedule)
    db.flush()
    return source, connector, schedule


def _success(db, source, connector, *, at):
    db.add(AcquisitionRun(
        source_id=source.id, source_connector_id=connector.id, run_key=f"alert-{uuid4()}",
        status="SUCCEEDED", completed_at=at,
    ))
    db.flush()


def _steps(db, source, connector, schedule, status, count, *, skipped=False):
    for _ in range(count):
        db.add(DiscoveryMissionSourceRun(
            schedule_id=schedule.id, source_id=source.id, source_connector_id=connector.id,
            wave_key="scheduled", attempt_number=1, run_key=f"alert-step-{uuid4()}", status=status,
            error_details={"scope_outcome": "SKIPPED"} if skipped else {}, created_at=NOW - timedelta(hours=1),
        ))
    db.flush()


def _alert(db, key):
    return db.scalar(select(DiscoveryAlert).where(DiscoveryAlert.alert_key == key, DiscoveryAlert.status != "RESOLVED"))


def test_stale_source_alert_opens_once_resolves_and_recurs_as_a_new_episode(db):  # noqa: F811
    source, connector, _s = _scope(db)
    key = f"stale-source:{source.id}"
    _success(db, source, connector, at=NOW - timedelta(hours=100))
    assert rules.evaluate_discovery_alerts(db, now=NOW)["opened"] >= 1
    first = _alert(db, key)
    assert first.status == "OPEN" and first.kind == "STALE_SOURCE" and first.subject_id == source.id
    assert rules.evaluate_discovery_alerts(db, now=NOW + timedelta(minutes=5))["opened"] == 0  # same episode
    assert db.scalars(select(DiscoveryAlert).where(DiscoveryAlert.alert_key == key)).all() == [first]
    _success(db, source, connector, at=NOW + timedelta(minutes=6))
    rules.evaluate_discovery_alerts(db, now=NOW + timedelta(minutes=10))
    db.refresh(first)
    assert first.status == "RESOLVED" and first.resolved_at is not None
    rules.evaluate_discovery_alerts(db, now=NOW + timedelta(hours=100))
    second = _alert(db, key)
    assert second is not None and second.id != first.id


def test_a_never_succeeded_source_gets_a_grace_period_equal_to_the_stale_window(db):  # noqa: F811
    new_source, _c, _s = _scope(db, created=NOW - timedelta(hours=10))
    old_source, _c2, _s2 = _scope(db, created=NOW - timedelta(hours=100))
    rules.evaluate_discovery_alerts(db, now=NOW)
    assert _alert(db, f"stale-source:{new_source.id}") is None
    assert _alert(db, f"stale-source:{old_source.id}") is not None


def test_stale_boundary_uses_72_hours(db):  # noqa: F811
    inside, c1, _s = _scope(db)
    outside, c2, _s2 = _scope(db)
    _success(db, inside, c1, at=NOW - timedelta(hours=71))
    _success(db, outside, c2, at=NOW - timedelta(hours=73))
    rules.evaluate_discovery_alerts(db, now=NOW)
    assert _alert(db, f"stale-source:{inside.id}") is None
    assert _alert(db, f"stale-source:{outside.id}") is not None


@pytest.mark.parametrize("failed,succeeded,skipped,expect", [
    (3, 2, 0, True),    # 3/5 = 60% with the minimum number of steps
    (4, 0, 0, False),   # fewer than 5 steps is not enough evidence
    (3, 3, 0, False),   # exactly 50% is not "above 50%"
    (3, 2, 50, True),   # skips are never counted either way
    (2, 3, 50, False),  # many skips do not make a healthy tool look failing
])
def test_tool_failure_rate_rule(db, failed, succeeded, skipped, expect):  # noqa: F811
    source, connector, schedule = _scope(db)
    _success(db, source, connector, at=NOW - timedelta(hours=1))  # keep this source out of the stale rule
    _steps(db, source, connector, schedule, "FAILED", failed)
    _steps(db, source, connector, schedule, "SUCCEEDED", succeeded)
    _steps(db, source, connector, schedule, "UNAVAILABLE", skipped, skipped=True)
    tool = str(connector.connector_type).strip().upper()
    conditions = rules.current_conditions(db, NOW)
    assert (f"tool-failure-rate:{tool}" in conditions) is expect


def test_new_alerts_email_one_digest_to_each_active_verified_administrator_without_opt_in(db):  # noqa: F811
    admin = _admin(db)
    unverified = _admin(db, verified=False)
    suspended = _admin(db, status="SUSPENDED")
    plain_user = _admin(db, grant=False)
    source, connector, _s = _scope(db)
    _success(db, source, connector, at=NOW - timedelta(hours=100))
    rules.evaluate_discovery_alerts(db, now=NOW)

    def note(user):
        return db.scalar(select(Notification).where(
            Notification.user_id == user.id, Notification.event_type == "ADMIN_DISCOVERY_ALERT"))

    sent = note(admin)
    assert sent is not None and source.name in sent.message
    delivery = db.scalar(select(NotificationEmailDelivery).where(NotificationEmailDelivery.notification_id == sent.id))
    assert delivery.status == "PENDING"  # operational email: no personal product-email opt-in needed
    assert db.scalar(select(func.count()).select_from(OutboxEvent).where(
        OutboxEvent.aggregate_id == sent.id, OutboxEvent.event_type == "NOTIFICATION_EMAIL_REQUESTED")) == 1
    unverified_delivery = db.scalar(select(NotificationEmailDelivery).where(
        NotificationEmailDelivery.notification_id == note(unverified).id))
    assert unverified_delivery.status == "SKIPPED_UNVERIFIED"
    assert note(suspended) is None and note(plain_user) is None
    # A repeat evaluation of the same episode sends nothing further.
    before = db.scalar(select(func.count()).select_from(Notification).where(Notification.event_type == "ADMIN_DISCOVERY_ALERT"))
    rules.evaluate_discovery_alerts(db, now=NOW + timedelta(minutes=5))
    assert db.scalar(select(func.count()).select_from(Notification).where(Notification.event_type == "ADMIN_DISCOVERY_ALERT")) == before


def test_alert_episode_and_admin_fanout_retry_atomically_after_partial_failure(
    db, monkeypatch,
):  # noqa: F811
    first_admin = _admin(db)
    second_admin = _admin(db)
    source, connector, _s = _scope(db)
    _success(db, source, connector, at=NOW - timedelta(hours=100))
    db.commit()
    original = rules._insert_notification
    calls = []

    def fail_second_recipient(session, values, *, commit=True):
        calls.append(values["user_id"])
        if len(calls) == 2:
            raise RuntimeError("simulated_notification_failure")
        return original(session, values, commit=commit)

    monkeypatch.setattr(rules, "_insert_notification", fail_second_recipient)
    with pytest.raises(RuntimeError, match="simulated_notification_failure"):
        rules.evaluate_discovery_alerts(db, now=NOW)
    db.rollback()
    key = f"stale-source:{source.id}"
    assert db.scalar(select(DiscoveryAlert.id).where(DiscoveryAlert.alert_key == key)) is None
    assert db.scalar(select(func.count()).select_from(Notification).where(
        Notification.event_type == "ADMIN_DISCOVERY_ALERT",
        Notification.user_id.in_([first_admin.id, second_admin.id]),
    )) == 0

    monkeypatch.setattr(rules, "_insert_notification", original)
    result = rules.evaluate_discovery_alerts(db, now=NOW + timedelta(minutes=1))
    assert result["opened"] == 1 and result["administrators_notified"] >= 2
    recipients = set(db.scalars(select(Notification.user_id).where(
        Notification.event_type == "ADMIN_DISCOVERY_ALERT",
        Notification.user_id.in_([first_admin.id, second_admin.id]),
    )))
    assert recipients == {first_admin.id, second_admin.id}


def test_email_is_sent_to_an_active_admin_and_refused_after_the_grant_is_revoked(db, monkeypatch):  # noqa: F811
    import app.application.notifications.service as service

    admin = _admin(db)
    source, connector, _s = _scope(db)
    _success(db, source, connector, at=NOW - timedelta(hours=100))
    rules.evaluate_discovery_alerts(db, now=NOW)
    notification = db.scalar(select(Notification).where(
        Notification.user_id == admin.id, Notification.event_type == "ADMIN_DISCOVERY_ALERT"))
    client = FakeSMTP()
    monkeypatch.setattr(service, "get_settings", settings)
    monkeypatch.setattr(service.smtplib, "SMTP", lambda *a, **k: client)
    notification_email_delivery_handler(factory(db))({"notification_id": str(notification.id)})
    assert len(client.messages) == 1 and client.messages[0]["To"] == admin.email
    assert source.name in client.messages[0].get_content()

    revoked = _admin(db)
    other, other_connector, _s2 = _scope(db)
    _success(db, other, other_connector, at=NOW - timedelta(hours=100))
    rules.evaluate_discovery_alerts(db, now=NOW + timedelta(hours=1))
    pending = db.scalar(select(Notification).where(
        Notification.user_id == revoked.id, Notification.event_type == "ADMIN_DISCOVERY_ALERT"))
    db.execute(AdminGrant.__table__.update().where(AdminGrant.user_id == revoked.id).values(active=False))
    db.flush()
    notification_email_delivery_handler(factory(db))({"notification_id": str(pending.id)})
    refused = db.scalar(select(NotificationEmailDelivery).where(NotificationEmailDelivery.notification_id == pending.id))
    assert refused.status == "SKIPPED_NOT_ADMIN" and len(client.messages) == 1


def _call(db, principal_id, method, path, **kwargs):
    client = _admin_test_client(db, principal_id)
    try:
        return getattr(client, method)(f"/api/v1/admin{path}", **kwargs)
    finally:
        app.dependency_overrides.pop(get_principal, None)
        app.dependency_overrides.pop(get_db, None)


def test_alert_api_lists_acknowledges_audits_and_rejects_invalid_transitions(db):  # noqa: F811
    admin = _admin(db)
    regular = _admin(db, grant=False)
    source, connector, _s = _scope(db)
    _success(db, source, connector, at=NOW - timedelta(hours=100))
    rules.evaluate_discovery_alerts(db, now=NOW)
    alert = _alert(db, f"stale-source:{source.id}")
    assert _call(db, regular.id, "get", "/discovery/alerts").status_code == 403
    assert _call(db, regular.id, "post", f"/discovery/alerts/{alert.id}/acknowledge", json={"reason": "taking this one"}).status_code == 403
    listing = _call(db, admin.id, "get", "/discovery/alerts?kind=STALE_SOURCE&limit=100").json()
    assert listing["rules"]["stale_after_hours"] == 72 and listing["rules"]["failure_min_steps"] == 5
    row = next(r for r in listing["data"] if r["id"] == str(alert.id))
    assert row["status"] == "OPEN" and row["source_id"] == str(source.id) and "url" not in str(row).lower()
    path = f"/discovery/alerts/{alert.id}/acknowledge"
    assert _call(db, admin.id, "post", path, json={"reason": "short"}).status_code == 422
    assert _call(db, admin.id, "post", f"/discovery/alerts/{uuid4()}/acknowledge", json={"reason": "taking this one"}).status_code == 404
    ok = _call(db, admin.id, "post", path, json={"reason": "provider contacted"})
    assert ok.status_code == 200 and ok.json()["data"]["status"] == "ACKNOWLEDGED"
    assert ok.json()["data"]["acknowledged_by_user_id"] == str(admin.id)
    assert _call(db, admin.id, "post", path, json={"reason": "provider contacted"}).status_code == 409
    assert db.scalar(select(func.count()).select_from(AdminAuditEvent).where(
        AdminAuditEvent.action == "DISCOVERY_ALERT_ACKNOWLEDGED", AdminAuditEvent.resource_id == alert.id)) == 1
    # Acknowledged alerts stay listed until the condition clears, and are not re-emailed.
    again = rules.evaluate_discovery_alerts(db, now=NOW + timedelta(minutes=5))
    assert _alert(db, f"stale-source:{source.id}").status == "ACKNOWLEDGED" and again["opened"] == 0
    _success(db, source, connector, at=NOW + timedelta(minutes=6))
    rules.evaluate_discovery_alerts(db, now=NOW + timedelta(minutes=10))
    resolved = _call(db, admin.id, "get", "/discovery/alerts?status=RESOLVED&limit=100").json()["data"]
    assert str(alert.id) in {r["id"] for r in resolved}
    assert _call(db, admin.id, "post", path, json={"reason": "provider contacted"}).status_code == 409
