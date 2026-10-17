import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.notifications.service import notification_email_delivery_handler
from app.infrastructure.db.models import (
    Notification,
    NotificationEmailDelivery,
    NotificationEmailPreference,
    User,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


@pytest.fixture
def db():
    engine = create_engine(os.environ["DATABASE_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def make_delivery(db):
    user = User(
        display_name="Email notification test",
        email=f"{uuid4()}@example.test",
        email_verified=True,
    )
    db.add(user)
    db.flush()
    notification = Notification(
        user_id=user.id,
        event_key=f"email-test:{uuid4()}",
        event_type="PUNT_CREATED",
        title="New Punt available",
        message="A new role is available.",
        resource_type="PUNT",
        resource_id=uuid4(),
        created_at=datetime.now(timezone.utc),
    )
    db.add(notification)
    db.flush()
    db.add(NotificationEmailPreference(
        user_id=user.id,
        enabled=True,
        updated_at=datetime.now(timezone.utc),
    ))
    delivery = NotificationEmailDelivery(
        notification_id=notification.id,
        status="PENDING",
        attempt_count=0,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(delivery)
    db.flush()
    return user, notification, delivery


class FakeSMTP:
    def __init__(self, *args, **kwargs):
        self.messages = []
        self.fail = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self, context=None):
        return None

    def login(self, username, password):
        return None

    def send_message(self, message):
        if self.fail:
            raise OSError("sensitive provider response must not be persisted")
        self.messages.append(message)


def settings():
    return SimpleNamespace(
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_username="mailer",
        smtp_password="secret",
        smtp_from_email="notifications@example.test",
        smtp_use_tls=True,
        smtp_timeout_seconds=10,
    )


def factory(db):
    return sessionmaker(
        bind=db.connection(),
        class_=Session,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )


def test_email_delivery_records_success_and_does_not_duplicate_sent_delivery(db, monkeypatch):
    import app.application.notifications.service as service

    _user, notification, delivery = make_delivery(db)
    client = FakeSMTP()
    monkeypatch.setattr(service, "get_settings", settings)
    monkeypatch.setattr(service.smtplib, "SMTP", lambda *a, **k: client)
    handle = notification_email_delivery_handler(factory(db))

    handle({"notification_id": str(notification.id)})
    handle({"notification_id": str(notification.id)})

    db.refresh(delivery)
    assert delivery.status == "SENT"
    assert delivery.attempt_count == 1
    assert len(client.messages) == 1
    assert client.messages[0]["To"].endswith("@example.test")
    assert client.messages[0]["Message-ID"] == (
        f"<jobpunter-notification-{notification.id}@jobpunter.local>"
    )


def test_email_delivery_persists_failure_without_removing_in_app_notification(db, monkeypatch):
    import app.application.notifications.service as service

    _user, notification, delivery = make_delivery(db)
    client = FakeSMTP()
    client.fail = True
    monkeypatch.setattr(service, "get_settings", settings)
    monkeypatch.setattr(service.smtplib, "SMTP", lambda *a, **k: client)
    handle = notification_email_delivery_handler(factory(db))

    with pytest.raises(RuntimeError, match="notification_email_delivery_failed"):
        handle({"notification_id": str(notification.id)})

    db.refresh(delivery)
    assert delivery.status == "FAILED"
    assert delivery.attempt_count == 1
    assert delivery.last_error == "OSError"
    assert db.get(Notification, notification.id) is not None
    assert "sensitive provider response" not in (delivery.last_error or "")


def test_failed_email_delivery_can_be_retried_to_sent_without_duplicate_notification(db, monkeypatch):
    import app.application.notifications.service as service

    _user, notification, delivery = make_delivery(db)
    client = FakeSMTP()
    client.fail = True
    monkeypatch.setattr(service, "get_settings", settings)
    monkeypatch.setattr(service.smtplib, "SMTP", lambda *a, **k: client)
    handle = notification_email_delivery_handler(factory(db))

    with pytest.raises(RuntimeError, match="notification_email_delivery_failed"):
        handle({"notification_id": str(notification.id)})

    db.refresh(delivery)
    assert delivery.status == "FAILED"
    assert delivery.attempt_count == 1
    assert db.get(Notification, notification.id) is not None

    client.fail = False
    handle({"notification_id": str(notification.id)})
    db.refresh(delivery)

    assert delivery.status == "SENT"
    assert delivery.attempt_count == 2
    assert delivery.sent_at is not None
    assert delivery.last_error is None
    assert len(client.messages) == 1
    assert db.scalar(select(func.count()).select_from(Notification).where(
        Notification.id == notification.id
    )) == 1


def test_email_delivery_records_unconfigured_provider_without_throwing(db, monkeypatch):
    import app.application.notifications.service as service

    _user, notification, delivery = make_delivery(db)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(smtp_host=None, smtp_from_email=None),
    )
    handle = notification_email_delivery_handler(factory(db))

    handle({"notification_id": str(notification.id)})

    db.refresh(delivery)
    assert delivery.status == "SKIPPED_UNCONFIGURED"
    assert delivery.attempt_count == 0
