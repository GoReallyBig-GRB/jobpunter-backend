from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.application.common.outbox import OutboxService
from app.infrastructure.db.models import AcquisitionRun, RawSourceRecord, Source, SourceConnector
from app.application.jobs.source_governance import (
    classify_acquisition_outcome,
    has_approved_permission_review,
    validate_operational_state,
)
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
                lease_expires_at=now + timedelta(hours=1),
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
            if source.source_type == "MANUAL_SUBMISSION" or has_approved_permission_review(source):
                source.operational_state, source.last_error_category = classify_acquisition_outcome(status="SUCCEEDED")
            else:
                source.operational_state = "RESTRICTED"
                source.last_error_category = "PERMISSION_REVIEW_REQUIRED"
            source.updated_at = now
        run.status = "SUCCEEDED"
        run.lease_expires_at = None
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
            if source.source_type == "MANUAL_SUBMISSION" or has_approved_permission_review(source):
                source.operational_state, source.last_error_category = classify_acquisition_outcome(
                    status="FAILED",
                    error_category=(error_summary.split(":", 1)[0] or "ACQUISITION_FAILED").upper()[:64],
                )
            else:
                source.operational_state = "RESTRICTED"
                source.last_error_category = "PERMISSION_REVIEW_REQUIRED"
            source.updated_at = now
        run.status = "FAILED"
        run.lease_expires_at = None
        run.completed_at = now
        run.metrics = {
            "acquired_count": acquired_count,
            "persisted_count": persisted_count,
        }
        run.error_summary = error_summary
        self.db.commit()


    def recover_stale_runs(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> int:
        """Fail expired acquisition leases so crashed workers cannot leave RUNNING forever."""
        if limit < 1:
            raise ValueError("recovery limit must be positive")
        observed_at = now or datetime.now(timezone.utc)
        stale_runs = self.db.scalars(
            select(AcquisitionRun)
            .where(
                AcquisitionRun.status == "RUNNING",
                AcquisitionRun.lease_expires_at.is_not(None),
                AcquisitionRun.lease_expires_at <= observed_at,
            )
            .order_by(AcquisitionRun.lease_expires_at, AcquisitionRun.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for run in stale_runs:
            source = self.db.get(Source, run.source_id)
            run.status = "FAILED"
            run.completed_at = observed_at
            run.lease_expires_at = None
            run.error_summary = "worker_lost:lease_expired"
            run.metrics = run.metrics or {}
            if source is not None:
                if source.source_type == "MANUAL_SUBMISSION" or has_approved_permission_review(source):
                    source.operational_state = "FAILED"
                    source.last_error_category = "WORKER_LOST"
                else:
                    source.operational_state = "RESTRICTED"
                    source.last_error_category = "PERMISSION_REVIEW_REQUIRED"
                source.last_observed_at = observed_at
                source.updated_at = observed_at
        self.db.commit()
        return len(stale_runs)

    def record_source_operational_state(
        self, source_id: UUID, state: str, error_category: str | None = None
    ) -> None:
        source = self.db.get(Source, source_id)
        if source is None:
            raise ValueError("source does not exist")
        source.operational_state = validate_operational_state(state)
        source.last_error_category = error_category[:64] if error_category else None
        source.updated_at = datetime.now(timezone.utc)
        self.db.commit()
