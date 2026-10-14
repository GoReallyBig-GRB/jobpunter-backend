"""Canonical JSON snapshot of versioned, human-reviewed job requirements."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import JobDescriptionVersion, JobRequirement, JobRequirementReviewEvent


def snapshot_job_requirements(
    db: Session,
    description_version_id: UUID | None,
) -> list[dict]:
    """Serialize requirements and latest review decisions for one immutable JD version."""
    if description_version_id is None:
        return []
    rows = db.scalars(
        select(JobRequirement)
        .where(JobRequirement.job_description_version_id == description_version_id)
        .order_by(JobRequirement.created_at, JobRequirement.id)
    ).all()
    if not rows:
        return []

    requirement_ids = [row.id for row in rows]
    events = db.scalars(
        select(JobRequirementReviewEvent)
        .where(JobRequirementReviewEvent.job_requirement_id.in_(requirement_ids))
        .order_by(
            JobRequirementReviewEvent.created_at.desc(),
            JobRequirementReviewEvent.id.desc(),
        )
    ).all()
    latest_event_by_requirement = {}
    for event in events:
        latest_event_by_requirement.setdefault(event.job_requirement_id, event)

    result = []
    for row in rows:
        event = latest_event_by_requirement.get(row.id)
        result.append({
            "id": str(row.id),
            "job_description_version_id": str(row.job_description_version_id),
            "requirement_type": row.requirement_type,
            "description": row.description,
            "importance": row.importance,
            "hard_constraint": row.hard_constraint,
            "extraction_status": row.extraction_status,
            "source_excerpt": row.source_excerpt,
            "extraction_confidence": row.extraction_confidence,
            "extraction_version": row.extraction_version,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "latest_review": ({
                "id": str(event.id),
                "reviewer_user_id": str(event.reviewer_user_id),
                "action": event.action,
                "previous_status": event.previous_status,
                "resulting_status": event.resulting_status,
                "note": event.note,
                "snapshot": event.snapshot,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            } if event else None),
        })
    return result
