from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from app.application.work.service import WorkService
from app.infrastructure.db.models import Base, OutboxEvent, WorkItem
from app.infrastructure.db.repositories.work import SqlAlchemyWorkRepository


def make_service():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    return engine, session, WorkService(SqlAlchemyWorkRepository(session))


def test_enqueue_creates_work_item_and_outbox_event_atomically():
    engine, session, service = make_service()
    try:
        result = service.enqueue(
            work_type="NORMALIZE_JOB",
            idempotency_key="raw-record-1:v1",
            payload={"raw_record_id": "raw-record-1"},
            resource_type="RawSourceRecord",
        )

        item_count = session.scalar(select(func.count()).select_from(WorkItem))
        event_count = session.scalar(select(func.count()).select_from(OutboxEvent))
        event = session.scalar(select(OutboxEvent))

        assert result.status == "PENDING"
        assert item_count == 1
        assert event_count == 1
        assert event.event_type == "WORK_REQUESTED"
        assert event.aggregate_id == result.work_id
        assert event.payload["work_item_id"] == str(result.work_id)
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_enqueue_is_idempotent_for_work_type_and_key():
    engine, session, service = make_service()
    try:
        first = service.enqueue(
            work_type="NORMALIZE_JOB",
            idempotency_key="raw-record-1:v1",
            payload={"raw_record_id": "raw-record-1"},
        )
        second = service.enqueue(
            work_type="NORMALIZE_JOB",
            idempotency_key="raw-record-1:v1",
            payload={"raw_record_id": "different-payload"},
        )

        assert first.work_id == second.work_id
        assert session.scalar(select(func.count()).select_from(WorkItem)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_enqueue_rejects_blank_idempotency_key():
    engine, session, service = make_service()
    try:
        try:
            service.enqueue(work_type="NORMALIZE_JOB", idempotency_key=" ", payload={})
        except ValueError as exc:
            assert "idempotency_key" in str(exc)
        else:
            raise AssertionError("blank idempotency key should be rejected")
        assert session.scalar(select(func.count()).select_from(WorkItem)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
