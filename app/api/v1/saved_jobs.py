"""Candidate-owned saved-job bookmarks."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.infrastructure.db.models.candidate import CandidateProfile
from app.infrastructure.db.models.jobs import Job
from app.infrastructure.db.models.opportunities import SavedJob
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate/saved-jobs", tags=["candidate saved jobs"])


def _saved_job_response(saved: SavedJob, job: Job) -> dict:
    return {
        "id": str(saved.id),
        "job_id": str(job.id),
        "job_title": job.canonical_title,
        "company_name": job.company_name,
        "saved_at": saved.saved_at.isoformat(),
    }


def _candidate_profile(db: Session, user_id: UUID) -> CandidateProfile:
    profile = db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
    if profile is None:
        raise HTTPException(status_code=404, detail="Candidate profile not found.")
    return profile


@router.get("")
def list_saved_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    profile = _candidate_profile(db, principal.user_id)
    rows = db.execute(
        select(SavedJob, Job)
        .join(Job, Job.id == SavedJob.job_id)
        .where(SavedJob.candidate_profile_id == profile.id)
        .order_by(SavedJob.saved_at.desc(), SavedJob.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {"data": {"items": [_saved_job_response(saved, job) for saved, job in rows]}}


@router.put("/{job_id}", status_code=status.HTTP_200_OK)
def save_job(
    job_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    profile = _candidate_profile(db, principal.user_id)
    job = db.get(Job, job_id)
    if job is None or job.lifecycle_status != "ACTIVE":
        raise HTTPException(status_code=404, detail="Active job not found.")
    existing = db.scalar(select(SavedJob).where(
        SavedJob.candidate_profile_id == profile.id, SavedJob.job_id == job.id
    ))
    if existing is not None:
        return {"data": _saved_job_response(existing, job), "created": False}
    saved = SavedJob(candidate_profile_id=profile.id, job_id=job.id)
    db.add(saved)
    try:
        db.commit()
        db.refresh(saved)
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(SavedJob).where(
            SavedJob.candidate_profile_id == profile.id, SavedJob.job_id == job.id
        ))
        if existing is None:
            raise
        saved = existing
    return {"data": _saved_job_response(saved, job), "created": saved is not existing if 'existing' in locals() else True}


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def unsave_job(
    job_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> None:
    profile = _candidate_profile(db, principal.user_id)
    saved = db.scalar(select(SavedJob).where(
        SavedJob.candidate_profile_id == profile.id, SavedJob.job_id == job_id
    ))
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved job not found.")
    db.delete(saved)
    db.commit()
