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
