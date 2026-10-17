"""Candidate visibility rules for canonical jobs and source provenance."""
from uuid import UUID

from sqlalchemy import and_, exists, func, not_, or_, select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import Job, JobProvenance, Source


def public_source_visibility_predicate():
    """A source is public only when active and explicitly permission-approved.

    Operational health is exposed to candidates but does not silently hide an already
    acquired job; freshness and closure are separate, job-level states.
    """
    return and_(
        Source.source_type != "MANUAL_SUBMISSION",
        Source.status == "ACTIVE",
        Source.permission_review_status == "APPROVED",
        Source.permission_review_reference.is_not(None),
        func.length(func.trim(Source.permission_review_reference)) > 0,
        Source.permission_reviewed_at.is_not(None),
    )


def candidate_job_visibility_predicate(user_id: UUID):
    """SQL predicate: approved shared source provenance OR this candidate's manual submission."""
    public_source = exists(
        select(1)
        .select_from(JobProvenance)
        .join(Source, Source.id == JobProvenance.source_id)
        .where(
            JobProvenance.job_id == Job.id,
            public_source_visibility_predicate(),
        )
    )
    owned_manual_submission = exists(
        select(1)
        .select_from(JobProvenance)
        .join(Source, Source.id == JobProvenance.source_id)
        .where(
            JobProvenance.job_id == Job.id,
            Source.source_type == "MANUAL_SUBMISSION",
            JobProvenance.submitted_by_user_id == user_id,
        )
    )
    foreign_manual_submission = exists(
        select(1)
        .select_from(JobProvenance)
        .join(Source, Source.id == JobProvenance.source_id)
        .where(
            JobProvenance.job_id == Job.id,
            Source.source_type == "MANUAL_SUBMISSION",
            JobProvenance.submitted_by_user_id.is_not(None),
            JobProvenance.submitted_by_user_id != user_id,
        )
    )
    # A legacy/manual-only canonical record with multiple submitters is ambiguous:
    # fail closed rather than exposing one candidate's private content to another.
    private_manual = and_(owned_manual_submission, not_(foreign_manual_submission))
    return or_(public_source, private_manual)


def candidate_can_access_job(db: Session, user_id: UUID, job_id: UUID) -> bool:
    """Fail closed for non-canonical, unprovenanced, restricted, or another user's private jobs."""
    return db.scalar(
        select(Job.id).where(
            Job.id == job_id,
            Job.identity_status == "CANONICAL",
            candidate_job_visibility_predicate(user_id),
        ).limit(1)
    ) is not None
