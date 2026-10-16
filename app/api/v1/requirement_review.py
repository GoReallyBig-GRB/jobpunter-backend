"""Reviewer-gated endpoints for the shared job-requirement review queue."""
import os
from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.jobs.requirement_review import (
    list_pending_requirements,
    list_requirement_review_history,
    review_requirement,
)
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/admin/job-requirements", tags=["job requirement review"])


def _require_reviewer(principal: AuthenticatedPrincipal) -> None:
    """Allow trusted Firebase identities in deployments; header identities only in opt-in dev."""
    if principal.auth_provider != "firebase":
        environment = os.getenv("JOBPUNTER_ENVIRONMENT", "").strip().lower()
        allow_dev_identity = os.getenv("JOBPUNTER_ALLOW_DEV_IDENTITY", "").strip().lower() == "true"
        if environment != "development" or not allow_dev_identity:
            raise HTTPException(
                status_code=503,
                detail="Requirement review requires trusted Firebase authentication outside development",
            )
    allowed = {
        value.strip()
        for value in os.getenv("JOBPUNTER_REQUIREMENT_REVIEWER_USER_IDS", "").split(",")
        if value.strip()
    }
    if not allowed or str(principal.user_id) not in allowed:
        raise HTTPException(status_code=403, detail="Requirement review access is not enabled for this user")


class RequirementDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(?i:confirm|reject)$")
    severity: Literal["MUST_HAVE", "IMPORTANT", "PREFERRED", "CONTEXTUAL"] | None = None
    note: str | None = Field(default=None, max_length=2000)


@router.get("", response_model=dict)
def get_review_queue(
    limit: int = Query(default=50, ge=1, le=200),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    _require_reviewer(principal)
    return {"data": list_pending_requirements(db, limit=limit)}


@router.get("/{requirement_id}/history", response_model=dict)
def get_requirement_history(
    requirement_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    _require_reviewer(principal)
    return {"data": list_requirement_review_history(db, requirement_id=requirement_id)}


@router.post("/{requirement_id}/decision", response_model=dict)
def decide_requirement(
    requirement_id: UUID,
    payload: RequirementDecisionRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    _require_reviewer(principal)
    return {
        "data": review_requirement(
            db,
            requirement_id=requirement_id,
            reviewer_user_id=principal.user_id,
            decision=payload.decision,
            note=payload.note,
            severity=payload.severity,
        )
    }
