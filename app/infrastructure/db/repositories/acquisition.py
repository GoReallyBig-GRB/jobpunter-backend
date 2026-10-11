from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.application.common.outbox import OutboxService
from app.infrastructure.db.models import AcquisitionRun, RawSourceRecord, Source, SourceConnector
from app.ports.acquisition import RawAcquisition


class SqlAlchemyAcquisitionRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_run(self, source_id: UUID, run_key: str) -> AcquisitionRun | None:
        return self.db.scalar(
            select(AcquisitionRun).where(
                AcquisitionRun.source_id == source_id,
                AcquisitionRun.run_key == run_key,
            )
        )

    def get_connector(self, connector_id: UUID) -> SourceConnector | None:
        return self.db.get(SourceConnector, connector_id)

    def get_source(self, source_id: UUID) -> Source | None:
        return self.db.get(Source, source_id)

    def create_run(
        self, source_id: UUID, connector_id: UUID, run_key: str
    ) -> tuple[AcquisitionRun, bool]:
        now = datetime.now(timezone.utc)
        run_id = uuid4()
        statement = (
            insert(AcquisitionRun)
            .values(
                id=run_id,
                source_id=source_id,
                source_connector_id=connector_id,
                run_key=run_key,
                status="RUNNING",
                started_at=now,
                completed_at=None,
                metrics={},
                error_summary=None,
                created_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_acquisition_run_source_key")
            .returning(AcquisitionRun.id)
        )
        created_id = self.db.scalar(statement)
        self.db.commit()

        run = self.db.get(AcquisitionRun, created_id) if created_id else self.get_run(source_id, run_key)
        if run is None:
            raise RuntimeError("Acquisition run could not be created or retrieved")
        return run, created_id is not None

    def persist_raw_if_new(self, run_id: UUID, item: RawAcquisition) -> bool:
        record_id = uuid4()
        deduplication_key = (
            f"id:{item.external_record_id}"
            if item.external_record_id is not None
            else f"hash:{item.content_hash}"
        )
        statement = (
            insert(RawSourceRecord)
            .values(
                id=record_id,
                acquisition_run_id=run_id,
                source_id=item.source_id,
                external_record_id=item.external_record_id,
                deduplication_key=deduplication_key,
                source_url=item.source_url,
                raw_content=item.raw_content,
                content_hash=item.content_hash,
                status="ACQUIRED",
                acquired_at=item.observed_at,
                created_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(constraint="uq_raw_source_dedup_key")
            .returning(RawSourceRecord.id)
        )
        persisted_id = self.db.scalar(statement)
        if persisted_id is None:
            return False

        OutboxService(self.db).append(
            event_type="RAW_SOURCE_RECORD_ACQUIRED",
            aggregate_type="RawSourceRecord",
            aggregate_id=persisted_id,
            payload={
                "raw_source_record_id": str(persisted_id),
                "source_id": str(item.source_id),
                "acquisition_run_id": str(run_id),
            },
        )
        return True

    def complete_run(self, run_id: UUID, acquired_count: int, persisted_count: int) -> None:
        run = self.db.get(AcquisitionRun, run_id)
        if run is None:
            raise ValueError("Acquisition run not found")
        now = datetime.now(timezone.utc)
        source = self.db.get(Source, run.source_id)
        if source is not None:
            source.last_observed_at = now
            source.updated_at = now
        run.status = "SUCCEEDED"
        run.completed_at = now
        run.metrics = {
            "acquired_count": acquired_count,
            "persisted_count": persisted_count,
        }
        run.error_summary = None
        self.db.commit()

    def fail_run(
        self,
        run_id: UUID,
        *,
        error_summary: str,
        acquired_count: int = 0,
        persisted_count: int = 0,
    ) -> None:
        # A failed statement may have left the current transaction unusable.
        # The run was committed before connector execution, so it can be updated separately.
        self.db.rollback()
        run = self.db.get(AcquisitionRun, run_id)
        if run is None:
            raise ValueError("Acquisition run not found")
        now = datetime.now(timezone.utc)
        source = self.db.get(Source, run.source_id)
        if source is not None:
            source.last_observed_at = now
            source.updated_at = now
        run.status = "FAILED"
        run.completed_at = now
        run.metrics = {
            "acquired_count": acquired_count,
            "persisted_count": persisted_count,
        }
        run.error_summary = error_summary
        self.db.commit()
