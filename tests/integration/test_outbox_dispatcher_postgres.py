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



def test_concurrent_postgres_dispatchers_claim_one_event_only():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    engine = create_engine(os.environ["DATABASE_URL"], pool_size=6)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    event_id = None
    barrier = Barrier(2)
    try:
        with SessionLocal() as seed:
            event = OutboxEvent(
                event_type="CLAIM_RACE", aggregate_type="Test", aggregate_id=uuid4(),
                payload={"value": "single-claim"}, status="PENDING", attempt_count=0,
                available_at=datetime.now(timezone.utc),
            )
            seed.add(event)
            seed.commit()
            event_id = event.id

        dispatcher = OutboxDispatcher(SessionLocal, {"CLAIM_RACE": lambda _payload: None})

        def claim():
            barrier.wait(timeout=10)
            return len(dispatcher.claim_batch(limit=1))

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(claim), executor.submit(claim)]
            claims = [future.result(timeout=20) for future in futures]
        assert sorted(claims) == [0, 1]

        with SessionLocal() as verify:
            saved = verify.get(OutboxEvent, event_id)
            assert saved.status == "PROCESSING"
            assert saved.attempt_count == 1
            assert saved.locked_at is not None
    finally:
        if event_id is not None:
            with SessionLocal() as cleanup:
                cleanup.query(OutboxEvent).filter(OutboxEvent.id == event_id).delete()
                cleanup.commit()
        engine.dispose()


def test_postgres_outbox_replays_after_ack_failure_and_requires_idempotent_handler():
    from sqlalchemy import event as sqlalchemy_event

    engine = create_engine(os.environ["DATABASE_URL"])
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    event_id = None
    effects = set()
    handler_calls = {"count": 0}
    fail_ack = {"armed": True}
    listener_registered = {"value": False}

    def fail_first_processed_commit(session):
        if not fail_ack["armed"]:
            return
        if any(
            isinstance(obj, OutboxEvent) and obj.status == "PROCESSED"
            for obj in session.dirty
        ):
            fail_ack["armed"] = False
            raise RuntimeError("simulated acknowledgement outage")

    try:
        with SessionLocal() as seed:
            outbox_event = OutboxEvent(
                event_type="IDEMPOTENT_EFFECT", aggregate_type="Test", aggregate_id=uuid4(),
                payload={"idempotency_key": "stable-effect-key"}, status="PENDING",
                attempt_count=0, available_at=datetime.now(timezone.utc),
            )
            seed.add(outbox_event)
            seed.commit()
            event_id = outbox_event.id

        def idempotent_handler(payload):
            handler_calls["count"] += 1
            effects.add(payload["idempotency_key"])

        dispatcher = OutboxDispatcher(
            SessionLocal, {"IDEMPOTENT_EFFECT": idempotent_handler}, lease_seconds=30,
        )
        sqlalchemy_event.listen(Session, "before_commit", fail_first_processed_commit)
        listener_registered["value"] = True
        with pytest.raises(RuntimeError, match="acknowledgement outage"):
            dispatcher.dispatch_batch(limit=1)
        sqlalchemy_event.remove(Session, "before_commit", fail_first_processed_commit)
        listener_registered["value"] = False

        with SessionLocal() as recovery:
            saved = recovery.get(OutboxEvent, event_id)
            assert saved.status == "PROCESSING"
            assert saved.attempt_count == 1
            saved.locked_at = datetime.now(timezone.utc) - timedelta(hours=1)
            recovery.commit()

        result = dispatcher.dispatch_batch(limit=1)
        assert result == {"claimed": 1, "processed": 1, "retrying": 0, "dead": 0, "other": 0}
        with SessionLocal() as verify:
            saved = verify.get(OutboxEvent, event_id)
            assert saved.status == "PROCESSED"
            assert saved.attempt_count == 2
            assert saved.locked_at is None
            assert handler_calls["count"] == 2
            assert effects == {"stable-effect-key"}
    finally:
        if listener_registered["value"]:
            sqlalchemy_event.remove(Session, "before_commit", fail_first_processed_commit)
        if event_id is not None:
            with SessionLocal() as cleanup:
                cleanup.query(OutboxEvent).filter(OutboxEvent.id == event_id).delete()
                cleanup.commit()
        engine.dispose()
