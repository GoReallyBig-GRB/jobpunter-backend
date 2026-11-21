"""A candidate's own job pool, synced from the central pool (never from external sources)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.candidates.service import CandidateService
from app.infrastructure.db.models import CandidatePoolEntry, Job
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate/pool", tags=["candidate-pool"])


@router.get("", response_model=dict)
def list_my_pool(
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    status: str = Query(default="ACTIVE", pattern="^(ACTIVE|CLOSED)$"),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    """Entries synced into this candidate's pool, newest match first."""
    profile = CandidateService(db).get_or_create_profile(principal.user_id)
    base = select(CandidatePoolEntry, Job).join(Job, Job.id == CandidatePoolEntry.job_id).where(
        CandidatePoolEntry.candidate_profile_id == profile.id,
        CandidatePoolEntry.status == status,
    )
    total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = db.execute(
        base.order_by(CandidatePoolEntry.matched_at.desc(), CandidatePoolEntry.id).limit(limit).offset(offset)
    ).all()
    return {
        "data": [
            {
                "job_id": str(job.id),
                "title": job.canonical_title,
                "company": job.company_name,
                "location": job.original_location_text or job.location_city or job.location_country,
                "work_mode": job.work_mode,
                "application_url": job.application_url,
                "freshness_status": job.freshness_status,
                "lifecycle_status": job.lifecycle_status,
                "last_observed_at": job.last_observed_at.isoformat() if job.last_observed_at else None,
                "match": entry.match_basis,
                "matched_at": entry.matched_at.isoformat(),
                "last_synced_at": entry.last_synced_at.isoformat(),
                "status": entry.status,
            }
            for entry, job in rows
        ],
        "pagination": {"limit": limit, "offset": offset, "total": total},
    }
