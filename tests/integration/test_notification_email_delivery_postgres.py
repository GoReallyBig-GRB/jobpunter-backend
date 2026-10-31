import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.common.outbox_dispatcher import OutboxDispatcher
from app.application.notifications.service import notification_email_delivery_handler, notification_email_retry_policy_provider
from app.application.notifications.smtp_config import encrypt_smtp_password
from app.infrastructure.db.models import (
    Notification,
    NotificationEmailDelivery,
    NotificationEmailPreference,
    OutboxEvent,
    SMTPProviderConfiguration,
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



def test_email_delivery_outbox_retries_are_bounded_and_dead_letter_truthfully(db, monkeypatch):
    import app.application.notifications.service as service

    _user, notification, delivery = make_delivery(db)
    client = FakeSMTP()
    client.fail = True
    monkeypatch.setattr(service, "get_settings", settings)
    monkeypatch.setattr(service.smtplib, "SMTP", lambda *a, **k: client)

    event = OutboxEvent(
        event_type="NOTIFICATION_EMAIL_REQUESTED",
        aggregate_type="Notification",
        aggregate_id=notification.id,
        payload={"notification_id": str(notification.id)},
        status="PENDING",
        attempt_count=0,
        available_at=datetime.now(timezone.utc),
    )
    db.add(event)
    db.flush()

    dispatcher = OutboxDispatcher(
        factory(db),
        {"NOTIFICATION_EMAIL_REQUESTED": notification_email_delivery_handler(factory(db))},
        max_attempts=2,
        base_backoff_seconds=1,
    )

    first = dispatcher.dispatch_batch(limit=1)
    assert first == {"claimed": 1, "processed": 0, "retrying": 1, "dead": 0, "other": 0}
    db.refresh(event)
    db.refresh(delivery)
    assert event.status == "PENDING"
    assert event.attempt_count == 1
    assert event.last_error == "handler_error:RuntimeError"
    assert delivery.status == "FAILED"
    assert delivery.attempt_count == 1

    # Make the scheduled retry due without sleeping.
    event.available_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    db.commit()

    second = dispatcher.dispatch_batch(limit=1)
    db.refresh(event)
    db.refresh(delivery)
    assert second == {"claimed": 1, "processed": 0, "retrying": 0, "dead": 1, "other": 0}
    assert event.status == "DEAD"
    assert event.attempt_count == 2
    assert event.last_error == "handler_error:RuntimeError"
    assert delivery.status == "FAILED"
    assert delivery.attempt_count == 2
    assert "sensitive provider response" not in event.last_error
    assert "sensitive provider response" not in (delivery.last_error or "")


@pytest.mark.parametrize(
    ("email_verified", "preference_enabled", "expected_status"),
    [
        (True, False, "DISABLED"),
        (False, True, "SKIPPED_UNVERIFIED"),
    ],
)
def test_email_delivery_never_opens_smtp_without_current_consent_and_verification(
    db, monkeypatch, email_verified, preference_enabled, expected_status
):
    import app.application.notifications.service as service

    user, notification, delivery = make_delivery(db)
    user.email_verified = email_verified
    preference = db.get(NotificationEmailPreference, user.id)
    preference.enabled = preference_enabled
    db.flush()

    monkeypatch.setattr(service, "get_settings", settings)
    monkeypatch.setattr(
        service.smtplib,
        "SMTP",
        lambda *args, **kwargs: pytest.fail(
            "SMTP must not be opened without consent and verification"
        ),
    )
    handle = notification_email_delivery_handler(factory(db))

    handle({"notification_id": str(notification.id)})

    db.refresh(delivery)
    assert delivery.status == expected_status
    assert delivery.attempt_count == 0
    assert delivery.sent_at is None


def test_email_delivery_falls_back_to_last_known_good_provider_and_records_version(db, monkeypatch):
    import app.application.notifications.service as service
    from cryptography.fernet import Fernet

    user, notification, delivery = make_delivery(db)
    monkeypatch.setenv(
        "JOBPUNTER_SMTP_SECRET_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )
    retired = SMTPProviderConfiguration(
        version=1, status="RETIRED", provider_name="previous",
        host="previous.smtp.test", port=587, use_tls=True, use_ssl=False,
        username="mailer", encrypted_password=encrypt_smtp_password("previous-secret"),
        from_email="previous@example.test", event_policy={}, retry_limit=3,
        retry_backoff_seconds=1, config_fingerprint="a" * 64,
        created_by_user_id=user.id, created_at=datetime.now(timezone.utc),
    )
    active = SMTPProviderConfiguration(
        version=2, status="PUBLISHED", provider_name="current",
        host="current.smtp.test", port=587, use_tls=True, use_ssl=False,
        username="mailer", encrypted_password=encrypt_smtp_password("current-secret"),
        from_email="current@example.test", event_policy={}, retry_limit=3,
        retry_backoff_seconds=1, config_fingerprint="b" * 64,
        created_by_user_id=user.id, published_by_user_id=user.id,
        created_at=datetime.now(timezone.utc), published_at=datetime.now(timezone.utc),
    )
    db.add_all([retired, active])
    db.flush()

    class ProviderSMTP(FakeSMTP):
        def __init__(self, host, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.host = host
            self.fail = host == "current.smtp.test"

        def send_message(self, message):
            if self.fail:
                raise OSError("current provider unavailable")
            self.messages.append(message)

    clients = []
    def smtp_factory(host, *args, **kwargs):
        client = ProviderSMTP(host, *args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(service, "get_settings", settings)
    monkeypatch.setattr(service.smtplib, "SMTP", smtp_factory)
    handle = notification_email_delivery_handler(factory(db))
    handle({"notification_id": str(notification.id)})

    db.refresh(delivery)
    assert delivery.status == "SENT"
    assert delivery.smtp_config_version == 1
    assert delivery.smtp_fallback_used is True
    assert [client.host for client in clients] == ["current.smtp.test", "previous.smtp.test"]
    assert clients[-1].messages[0]["From"] == "previous@example.test"


def test_admin_smtp_retry_policy_is_used_for_notification_outbox_events(db, monkeypatch):
    from cryptography.fernet import Fernet

    user = User(display_name="SMTP retry policy owner")
    db.add(user)
    db.flush()
    monkeypatch.setenv(
        "JOBPUNTER_SMTP_SECRET_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )
    config = SMTPProviderConfiguration(
        version=1, status="PUBLISHED", provider_name="retry-policy",
        host="smtp.example.test", port=587, use_tls=True, use_ssl=False,
        username="mailer", encrypted_password=encrypt_smtp_password("secret"),
        from_email="noreply@example.test", event_policy={}, retry_limit=4,
        retry_backoff_seconds=37, config_fingerprint="c" * 64,
        created_by_user_id=user.id, published_by_user_id=user.id,
        created_at=datetime.now(timezone.utc), published_at=datetime.now(timezone.utc),
    )
    db.add(config)
    db.flush()
    policy_provider = notification_email_retry_policy_provider(factory(db))
    assert policy_provider("NOTIFICATION_EMAIL_REQUESTED") == (4, 37)
    assert policy_provider("OTHER_EVENT") is None


def test_admin_smtp_event_policy_can_disable_a_notification_event(db, monkeypatch):
    import app.application.notifications.service as service
    from cryptography.fernet import Fernet

    user, notification, delivery = make_delivery(db)
    monkeypatch.setenv(
        "JOBPUNTER_SMTP_SECRET_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )
    config = SMTPProviderConfiguration(
        version=1, status="PUBLISHED", provider_name="policy",
        host="smtp.example.test", port=587, use_tls=True, use_ssl=False,
        username="mailer", encrypted_password=encrypt_smtp_password("secret"),
        from_email="noreply@example.test", event_policy={"PUNT_CREATED": False},
        retry_limit=3, retry_backoff_seconds=10, config_fingerprint="d" * 64,
        created_by_user_id=user.id, published_by_user_id=user.id,
        created_at=datetime.now(timezone.utc), published_at=datetime.now(timezone.utc),
    )
    db.add(config)
    db.flush()

    def should_not_send(*args, **kwargs):
        raise AssertionError("disabled event must not contact SMTP")

    monkeypatch.setattr(service.smtplib, "SMTP", should_not_send)
    handle = notification_email_delivery_handler(factory(db))
    handle({"notification_id": str(notification.id)})
    db.refresh(delivery)
    assert delivery.status == "DISABLED"
    assert delivery.attempt_count == 0
