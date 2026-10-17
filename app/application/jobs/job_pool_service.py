"""Read-only candidate access to shared and candidate-submitted canonical jobs."""
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.jobs.access import (
    candidate_can_access_job,
    candidate_job_visibility_predicate,
    public_source_visibility_predicate,
)
from app.application.jobs.job_pool_schemas import (
    JobPoolDescription,
    JobPoolDescriptionPreview,
    JobPoolDetail,
    JobPoolItem,
    JobPoolPagination,
    JobPoolProvenance,
)
from app.infrastructure.db.models import Job, JobDescriptionVersion, JobProvenance, Source


class JobPoolService:
    def __init__(self, db: Session):
        self.db = db

    def _descriptions(self, job_ids: list[UUID]) -> dict[UUID, JobDescriptionVersion]:
        if not job_ids:
            return {}
        rows = self.db.scalars(
            select(JobDescriptionVersion)
            .where(JobDescriptionVersion.job_id.in_(job_ids))
            .order_by(
                JobDescriptionVersion.job_id,
                JobDescriptionVersion.version_number.desc(),
                JobDescriptionVersion.id.desc(),
            )
        ).all()
        latest: dict[UUID, JobDescriptionVersion] = {}
        for row in rows:
            latest.setdefault(row.job_id, row)
        return latest

    def _provenance(self, job_ids: list[UUID], user_id: UUID) -> dict[UUID, list[JobPoolProvenance]]:
        if not job_ids:
            return {}
        rows = self.db.execute(
            select(JobProvenance, Source.name, Source.source_type, Source.operational_state)
            .join(Source, Source.id == JobProvenance.source_id)
            .where(
                JobProvenance.job_id.in_(job_ids),
                or_(
                    public_source_visibility_predicate(),
                    (
                        (Source.source_type == "MANUAL_SUBMISSION")
                        & (JobProvenance.submitted_by_user_id == user_id)
                    ),
                ),
            )
            .order_by(JobProvenance.observed_at.desc(), JobProvenance.id.desc())
        ).all()
        result: dict[UUID, list[JobPoolProvenance]] = {}
        seen: set[tuple[UUID, UUID]] = set()
        for provenance, source_name, source_type, source_operational_state in rows:
            key = (provenance.job_id, provenance.source_id)
            if key in seen:
                continue
            seen.add(key)
            result.setdefault(provenance.job_id, []).append(
                JobPoolProvenance(
                    source_id=provenance.source_id,
                    source_name=source_name,
                    source_type=source_type,
                    source_operational_state=source_operational_state,
                    source_url=provenance.source_url,
                    observed_at=provenance.observed_at,
                    observation_type=provenance.observation_type,
                    status=provenance.status,
                )
            )
        return result

    @staticmethod
    def _description_payload(row: JobDescriptionVersion | None, *, detail: bool):
        if row is None:
            return None
        common = {
            "version_number": row.version_number,
            "preview": row.content[:320],
            "content_hash": row.content_hash,
            "completeness_status": row.completeness_status,
            "retrieved_at": row.retrieved_at,
            "requirement_extraction_status": row.requirement_extraction_status,
        }
        if detail:
            common["content"] = row.content
            return JobPoolDescription(**common)
        return JobPoolDescriptionPreview(**common)

    def _item(
        self,
        job: Job,
        description: JobDescriptionVersion | None,
        provenance: list[JobPoolProvenance],
        *,
        detail: bool = False,
    ):
        return (JobPoolDetail if detail else JobPoolItem)(
            id=job.id,
            title=job.canonical_title,
            company_name=job.company_name,
            employment_type=job.employment_type,
            salary_min=float(job.salary_min) if job.salary_min is not None else None,
            salary_max=float(job.salary_max) if job.salary_max is not None else None,
            salary_currency=job.salary_currency,
            salary_period=job.salary_period,
            location_country=job.location_country,
            location_region=job.location_region,
            location_city=job.location_city,
            original_location_text=job.original_location_text,
            work_mode=job.work_mode,
            remote_eligibility=job.remote_eligibility,
            location_restrictions=job.location_restrictions or {},
            application_url=job.application_url,
            lifecycle_status=job.lifecycle_status or "UNKNOWN",
            freshness_status=job.freshness_status or "UNKNOWN",
            first_discovered_at=job.first_discovered_at,
            last_observed_at=job.last_observed_at,
            last_verified_at=job.last_verified_at,
            description=self._description_payload(description, detail=detail),
            provenance=provenance,
        )

    def list_jobs(
        self,
        user_id: UUID,
        *,
        limit: int = 20,
        offset: int = 0,
        query: str | None = None,
        lifecycle_status: str | None = None,
        freshness_status: str | None = None,
        location_country: str | None = None,
        work_mode: str | None = None,
    ) -> dict:
        if limit < 1 or limit > 50 or offset < 0:
            raise DomainError("INVALID_PAGINATION", "limit must be 1–50 and offset must be non-negative", 422)
        filters = [
            Job.identity_status == "CANONICAL",
            candidate_job_visibility_predicate(user_id),
        ]
        if lifecycle_status:
            filters.append(Job.lifecycle_status == lifecycle_status.strip().upper())
        if freshness_status:
            filters.append(Job.freshness_status == freshness_status.strip().upper())
        if location_country:
            filters.append(func.lower(func.coalesce(Job.location_country, "")) == location_country.strip().lower())
        if work_mode:
            filters.append(func.lower(func.coalesce(Job.work_mode, "")) == work_mode.strip().lower())
        if query:
            pattern = f"%{query.strip()}%"
            filters.append(or_(
                func.coalesce(Job.canonical_title, "").ilike(pattern),
                func.coalesce(Job.company_name, "").ilike(pattern),
                func.coalesce(Job.original_location_text, "").ilike(pattern),
                func.coalesce(Job.location_city, "").ilike(pattern),
                func.coalesce(Job.location_region, "").ilike(pattern),
                func.coalesce(Job.location_country, "").ilike(pattern),
            ))
        base = select(Job).where(*filters)
        total = int(self.db.scalar(select(func.count()).select_from(base.subquery())) or 0)
        jobs = list(self.db.scalars(
            base.order_by(Job.last_observed_at.desc(), Job.id.desc())
            .offset(offset).limit(limit)
        ).all())
        descriptions = self._descriptions([job.id for job in jobs])
        provenance = self._provenance([job.id for job in jobs], user_id)
        items = [
            self._item(job, descriptions.get(job.id), provenance.get(job.id, []))
            for job in jobs
        ]
        return {
            "data": items,
            "pagination": JobPoolPagination(limit=limit, offset=offset, total=total),
        }

    def get_job(self, user_id: UUID, job_id: UUID) -> JobPoolDetail:
        if not candidate_can_access_job(self.db, user_id, job_id):
            raise DomainError("JOB_NOT_FOUND", "Job not found", 404)
        job = self.db.scalar(select(Job).where(
            Job.id == job_id,
            Job.identity_status == "CANONICAL",
        ))
        if job is None:
            raise DomainError("JOB_NOT_FOUND", "Job not found", 404)
        description = self._descriptions([job.id]).get(job.id)
        provenance = self._provenance([job.id], user_id).get(job.id, [])
        return self._item(job, description, provenance, detail=True)
