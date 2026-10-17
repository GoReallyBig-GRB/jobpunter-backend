"""Candidate-facing canonical Job Pool endpoints."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.jobs.job_pool_schemas import JobPoolDetailResponse, JobPoolListResponse
from app.application.jobs.job_pool_service import JobPoolService
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate/jobs/pool", tags=["candidate-job-pool"])


@router.get("", response_model=JobPoolListResponse)
def list_candidate_job_pool(
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    query: str | None = Query(default=None, min_length=1, max_length=120),
    lifecycle_status: str | None = Query(default=None, max_length=32),
    freshness_status: str | None = Query(default=None, max_length=32),
    location_country: str | None = Query(default=None, max_length=128),
    work_mode: str | None = Query(default=None, max_length=32),
    employment_type: str | None = Query(default=None, max_length=64),
    seniority: str | None = Query(default=None, max_length=64),
    salary_min: float | None = Query(default=None, ge=0),
    salary_max: float | None = Query(default=None, ge=0),
    salary_currency: str | None = Query(default=None, min_length=3, max_length=8),
    salary_period: str | None = Query(default=None, max_length=32),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    return JobPoolService(db).list_jobs(
        principal.user_id,
        limit=limit,
        offset=offset,
        query=query,
        lifecycle_status=lifecycle_status,
        freshness_status=freshness_status,
        location_country=location_country,
        work_mode=work_mode,
        employment_type=employment_type,
        seniority=seniority,
        salary_min=salary_min,
        salary_max=salary_max,
        salary_currency=salary_currency,
        salary_period=salary_period,
    )


@router.get("/{job_id}", response_model=JobPoolDetailResponse)
def get_candidate_job_pool_item(
    job_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    return {"data": JobPoolService(db).get_job(principal.user_id, job_id)}
