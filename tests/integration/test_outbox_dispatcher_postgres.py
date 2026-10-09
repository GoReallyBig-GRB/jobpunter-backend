import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.common.outbox_dispatcher import OutboxDispatcher
from app.infrastructure.db.models import OutboxEvent


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


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="PostgreSQL integration test requires DATABASE_URL",
)


def test_outbox_dispatches_and_recovers_stale_processing_lease(db):
    now = datetime.now(timezone.utc)
    event = OutboxEvent(
        event_type="TEST_EVENT", aggregate_type="Test", aggregate_id=uuid4(),
        payload={"value": 42}, status="PROCESSING", attempt_count=0,
        available_at=now, locked_at=now - timedelta(hours=1),
    )
    db.add(event)
    db.flush()
    event_id = event.id

    factory = sessionmaker(
        bind=db.connection(), class_=Session, join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    handled = []
    dispatcher = OutboxDispatcher(
        factory, {"TEST_EVENT": lambda payload: handled.append(payload["value"])},
        lease_seconds=30,
    )

    result = dispatcher.dispatch_batch()
    assert result == {"claimed": 1, "processed": 1, "retrying": 0, "dead": 0, "other": 0}
    assert handled == [42]
    db.expire_all()
    saved = db.get(OutboxEvent, event_id)
    assert saved.status == "PROCESSED"
    assert saved.locked_at is None
    assert saved.processed_at is not None


def test_outbox_failure_is_retried_without_leaking_exception_text(db):
    event = OutboxEvent(
        event_type="BROKEN_EVENT", aggregate_type="Test", aggregate_id=uuid4(),
        payload={}, status="PENDING", attempt_count=0,
        available_at=datetime.now(timezone.utc),
    )
    db.add(event)
    db.flush()
    event_id = event.id
    factory = sessionmaker(
        bind=db.connection(), class_=Session, join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )

    def fail(_payload):
        raise RuntimeError("secret token should not be persisted")

    dispatcher = OutboxDispatcher(factory, {"BROKEN_EVENT": fail}, max_attempts=2)
    result = dispatcher.dispatch_batch()
    assert result["retrying"] == 1
    db.expire_all()
    saved = db.get(OutboxEvent, event_id)
    assert saved.status == "PENDING"
    assert saved.attempt_count == 1
    assert saved.last_error == "handler_error:RuntimeError"
    assert "secret token" not in saved.last_error
