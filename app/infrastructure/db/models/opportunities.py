import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin


class CandidateEvaluation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "candidate_evaluations"
    __table_args__ = (UniqueConstraint(
        "candidate_profile_id", "job_id", "target_role_key", "evaluation_version",
        name="uq_evaluation_candidate_job_role_version",
    ),)

    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    target_role_key: Mapped[str] = mapped_column(String(255), nullable=False)
    evaluation_version: Mapped[int] = mapped_column(nullable=False)
    validity_status: Mapped[str] = mapped_column(String(32), nullable=False, default="CURRENT")
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    hard_blockers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    evidence_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    explanation: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_profile_revision: Mapped[int] = mapped_column(nullable=False, default=1)
    preferences_revision: Mapped[int] = mapped_column(nullable=False, default=0)
    job_description_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_description_versions.id", ondelete="RESTRICT")
    )
    input_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class Punt(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "punts"
    __table_args__ = (UniqueConstraint("candidate_profile_id", "job_id", name="uq_punt_candidate_job"),)

    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    qualified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class PuntEvaluation(Base):
    __tablename__ = "punt_evaluations"

    punt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("punts.id", ondelete="CASCADE"), primary_key=True)
    evaluation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_evaluations.id", ondelete="RESTRICT"), primary_key=True)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class Application(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("candidate_profile_id", "job_id", name="uq_application_candidate_job"),)

    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    punt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("punts.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PLANNED")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class ApplicationEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "application_events"

    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column()
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
