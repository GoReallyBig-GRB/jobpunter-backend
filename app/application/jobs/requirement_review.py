"""Human review workflow for AI-extracted job requirements."""
from __future__ import annotations

import uuid
from sqlalchemy import select
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.infrastructure.db.models import Job, JobDescriptionVersion, JobRequirement
from app.infrastructure.db.models.requirement_review import JobRequirementReviewEvent


def list_pending_requirements(db: Session, *, limit: int = 50) -> list[dict]:
    if not 1 <= limit <= 200:
        raise DomainError("INVALID_LIMIT", "limit must be between 1 and 200", 422)
    rows = db.execute(
        select(JobRequirement, JobDescriptionVersion, Job)
        .join(JobDescriptionVersion, JobRequirement.job_description_version_id == JobDescriptionVersion.id)
        .join(Job, JobDescriptionVersion.job_id == Job.id)
        .where(JobRequirement.extraction_status == "NEEDS_REVIEW")
        .order_by(JobRequirement.created_at.asc())
        .limit(limit)
    ).all()
    return [
        {
            "id": str(requirement.id),
            "job_id": str(job.id),
            "job_title": job.canonical_title,
            "company_name": job.company_name,
            "description_version_id": str(description.id),
            "requirement_type": requirement.requirement_type,
            "description": requirement.description,
            "importance": requirement.importance,
            "hard_constraint": requirement.hard_constraint,
            "source_excerpt": requirement.source_excerpt,
            "extraction_confidence": requirement.extraction_confidence,
            "extraction_version": requirement.extraction_version,
            "created_at": requirement.created_at.isoformat() if requirement.created_at else None,
        }
        for requirement, description, job in rows
    ]


def review_requirement(
    db: Session,
    *,
    requirement_id: uuid.UUID,
    reviewer_user_id: uuid.UUID,
    decision: str,
    note: str | None = None,
) -> dict:
    decision = decision.strip().upper()
    status_by_decision = {"CONFIRM": "CONFIRMED", "REJECT": "REJECTED"}
    if decision not in status_by_decision:
        raise DomainError("INVALID_REVIEW_DECISION", "decision must be CONFIRM or REJECT", 422)
    if note is not None:
        note = note.strip()
        if len(note) > 2000:
            raise DomainError("NOTE_TOO_LONG", "note must be 2000 characters or fewer", 422)
    requirement = db.scalar(
        select(JobRequirement).where(JobRequirement.id == requirement_id).with_for_update()
    )
    if requirement is None:
        raise DomainError("NOT_FOUND", "Job requirement not found", 404)
    if requirement.extraction_status != "NEEDS_REVIEW":
        raise DomainError(
            "REQUIREMENT_ALREADY_REVIEWED",
            "Only requirements in NEEDS_REVIEW can be decided; use a new extraction/review cycle to reconsider it",
            409,
            {"current_status": requirement.extraction_status},
        )
    previous_status = requirement.extraction_status
    resulting_status = status_by_decision[decision]
    snapshot = {
        "requirement_type": requirement.requirement_type,
        "description": requirement.description,
        "importance": requirement.importance,
        "hard_constraint": requirement.hard_constraint,
        "source_excerpt": requirement.source_excerpt,
        "extraction_confidence": requirement.extraction_confidence,
        "extraction_version": requirement.extraction_version,
    }
    requirement.extraction_status = resulting_status
    event = JobRequirementReviewEvent(
        job_requirement_id=requirement.id,
        reviewer_user_id=reviewer_user_id,
        action=decision,
        previous_status=previous_status,
        resulting_status=resulting_status,
        note=note or None,
        snapshot=snapshot,
    )
    db.add(event)
    db.commit()
    db.refresh(requirement)
    return {
        "id": str(requirement.id),
        "extraction_status": requirement.extraction_status,
        "review_event_id": str(event.id),
        "reviewer_user_id": str(reviewer_user_id),
        "reviewed_at": event.created_at.isoformat() if event.created_at else None,
        "note": event.note,
    }


def list_requirement_review_history(db: Session, *, requirement_id: uuid.UUID) -> list[dict]:
    """Return the immutable decision history for one requirement."""
    requirement = db.scalar(
        select(JobRequirement.id).where(JobRequirement.id == requirement_id)
    )
    if requirement is None:
        raise DomainError("NOT_FOUND", "Job requirement not found", 404)
    events = db.scalars(
        select(JobRequirementReviewEvent)
        .where(JobRequirementReviewEvent.job_requirement_id == requirement_id)
        .order_by(desc(JobRequirementReviewEvent.created_at), desc(JobRequirementReviewEvent.id))
    ).all()
    return [
        {
            "id": str(event.id),
            "reviewer_user_id": str(event.reviewer_user_id),
            "action": event.action,
            "previous_status": event.previous_status,
            "resulting_status": event.resulting_status,
            "note": event.note,
            "snapshot": event.snapshot,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
        for event in events
    ]
