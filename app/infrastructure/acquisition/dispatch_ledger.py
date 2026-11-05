"""PostgreSQL-backed durable adaptive-dispatch attempt ledger."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.jobs.adaptive_dispatcher import DispatchAttempt
from app.infrastructure.db.models.source_intelligence import AcquisitionDispatchAttempt


class SqlAlchemyDispatchAttemptLedger:
    def __init__(self, db: Session, *, profile_id: UUID | None = None) -> None:
        self.db = db
        self.profile_id = profile_id

    def was_attempted(self, attempt_key: str) -> bool:
        return (
            self.db.scalar(
                select(AcquisitionDispatchAttempt.id).where(
                    AcquisitionDispatchAttempt.attempt_key == attempt_key
                )
            )
            is not None
        )

    def record(self, attempt: DispatchAttempt) -> None:
        self.db.add(
            AcquisitionDispatchAttempt(
                attempt_key=attempt.attempt_key,
                profile_id=self.profile_id,
                url_sha256=attempt.url_sha256,
                route=attempt.route.value,
                status=attempt.status.value,
                error_category=attempt.error_category,
                acquired_count=attempt.acquired_count,
            )
        )
        self.db.flush()
