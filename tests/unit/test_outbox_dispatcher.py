"""Compatibility tests for the legacy repository-port dispatcher, not production worker acceptance."""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.application.work.dispatcher import OutboxDispatcher
from app.application.work.service import WorkService
from app.infrastructure.db.models import Base, OutboxEvent, WorkItem
from app.infrastructure.db.repositories.outbox import SqlAlchemyOutboxRepository
from app.infrastructure.db.repositories.work import SqlAlchemyWorkRepository


def make_fixture():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    work_service = WorkService(SqlAlchemyWorkRepository(session))
    return engine, session, work_service


def test_dispatcher_processes_and_acknowledges_event():
    engine, session, work_service = make_fixture()
    handled = []
    try:
        work_service.enqueue(
            work_type="NORMALIZE_JOB",
            idempotency_key="raw-1",
            payload={"raw_record_id": "raw-1"},
        )
        dispatcher = OutboxDispatcher(
            SqlAlchemyOutboxRepository(session),
            {"WORK_REQUESTED": lambda payload: handled.append(payload["work_type"])},
        )

        result = dispatcher.dispatch_once()

        event = session.scalar(select(OutboxEvent))
        item = session.scalar(select(WorkItem))
        assert result.claimed == 1
        assert result.processed == 1
        assert result.rescheduled == 0
        assert event.status == "PROCESSED"
        assert event.processed_at is not None
        assert item.status == "SUCCEEDED"
        assert handled == ["NORMALIZE_JOB"]
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_dispatcher_stores_safe_failure_category_and_stops_at_attempt_limit():
    engine, session, work_service = make_fixture()
    try:
        work_service.enqueue(
            work_type="NORMALIZE_JOB",
            idempotency_key="raw-2",
            payload={"raw_record_id": "raw-2"},
        )

        def broken_handler(_payload):
            raise RuntimeError("example private value must not be stored")

        dispatcher = OutboxDispatcher(
            SqlAlchemyOutboxRepository(session),
            {"WORK_REQUESTED": broken_handler},
            max_attempts=1,
            base_delay_seconds=1,
            max_delay_seconds=1,
        )
        result = dispatcher.dispatch_once()

        event = session.scalar(select(OutboxEvent))
        item = session.scalar(select(WorkItem))
        assert result.claimed == 1
        assert result.failed == 1
        assert result.rescheduled == 0
        assert event.status == "FAILED"
        assert event.last_error == "RuntimeError"
        assert item.status == "FAILED"
        assert item.error_code == "RuntimeError"
        assert "example private value" not in event.last_error
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()



def test_dispatcher_reschedules_then_dead_letters_after_attempt_limit():
    engine, session, work_service = make_fixture()
    calls = []
    try:
        work_service.enqueue(
            work_type="NORMALIZE_JOB",
            idempotency_key="retry-1",
            payload={"raw_record_id": "retry-1"},
        )

        def broken_handler(payload):
            calls.append(payload["work_type"])
            raise RuntimeError("sensitive retry detail")

        dispatcher = OutboxDispatcher(
            SqlAlchemyOutboxRepository(session),
            {"WORK_REQUESTED": broken_handler},
            max_attempts=2,
            base_delay_seconds=1,
            max_delay_seconds=2,
        )
        first = dispatcher.dispatch_once()
        event = session.scalar(select(OutboxEvent))
        item = session.scalar(select(WorkItem))
        assert first.rescheduled == 1
        assert first.failed == 0
        assert event.status == "PENDING"
        assert event.attempt_count == 1
        assert event.last_error == "RuntimeError"
        assert item.status == "PENDING"
        assert item.error_code == "RuntimeError"

        # Advance the scheduled retry to due time without sleeping.
        from datetime import datetime, timezone
        event.available_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        session.commit()

        second = dispatcher.dispatch_once()
        session.refresh(event)
        session.refresh(item)
        assert second.failed == 1
        assert second.rescheduled == 0
        assert event.status == "FAILED"
        assert event.attempt_count == 2
        assert event.last_error == "RuntimeError"
        assert item.status == "FAILED"
        assert item.error_code == "RuntimeError"
        assert "sensitive retry detail" not in str(item.error_details)
        assert calls == ["NORMALIZE_JOB", "NORMALIZE_JOB"]
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_dispatcher_recovers_expired_processing_lease():
    from datetime import datetime, timezone

    engine, session, work_service = make_fixture()
    handled = []
    try:
        work_service.enqueue(
            work_type="NORMALIZE_JOB",
            idempotency_key="lease-recovery-1",
            payload={"raw_record_id": "lease-recovery-1"},
        )
        repository = SqlAlchemyOutboxRepository(session)
        first_claim = repository.claim_due(limit=1, lease_seconds=120)
        assert len(first_claim) == 1
        event = session.scalar(select(OutboxEvent))
        assert event.status == "PROCESSING"
        assert event.attempt_count == 1

        # Simulate worker death and lease expiry.
        event.available_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        session.commit()

        dispatcher = OutboxDispatcher(
            repository,
            {"WORK_REQUESTED": lambda payload: handled.append(payload["work_type"])},
        )
        result = dispatcher.dispatch_once()
        session.refresh(event)
        item = session.scalar(select(WorkItem))
        assert result.claimed == 1
        assert result.processed == 1
        assert event.status == "PROCESSED"
        assert event.attempt_count == 2
        assert item.status == "SUCCEEDED"
        assert handled == ["NORMALIZE_JOB"]
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
