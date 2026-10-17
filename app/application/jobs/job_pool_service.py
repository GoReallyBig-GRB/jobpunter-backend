"""Read-only candidate access to shared and candidate-submitted canonical jobs."""
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.jobs.access import (
    candidate_can_access_job,
    candidate_job_visibility_predicate,
    public_source_visibility_predicate,
)
from app.application.jobs.job_pool_schemas import (
    JobPoolDescription, JobPoolDescriptionPreview, JobPoolDescriptionVersionSummary,
    JobPoolDetail, JobPoolDiscoveryEvent, JobPoolItem, JobPoolPagination,
    JobPoolProvenance, JobPoolRequirement, JobPoolVerification,
)
from app.infrastructure.db.models import (
    CandidateEvaluation, CandidateProfile, Job, JobDescriptionVersion,
    JobDiscoveryEvent, JobProvenance, JobRequirement, JobVerificationAttempt,
    RawSourceRecord, Source,
)


class JobPoolService:
    def __init__(self, db: Session):
        self.db = db

    def _descriptions(self, job_ids: list[UUID]) -> dict[UUID, JobDescriptionVersion]:
        if not job_ids:
            return {}
        ranked = select(
            JobDescriptionVersion.id.label("description_id"),
            func.row_number().over(
                partition_by=JobDescriptionVersion.job_id,
                order_by=(JobDescriptionVersion.version_number.desc(), JobDescriptionVersion.id.desc()),
            ).label("row_number"),
        ).where(JobDescriptionVersion.job_id.in_(job_ids)).subquery()
        rows = self.db.scalars(
            select(JobDescriptionVersion)
            .join(ranked, ranked.c.description_id == JobDescriptionVersion.id)
            .where(ranked.c.row_number == 1)
        ).all()
        return {row.job_id: row for row in rows}

    def _provenance(self, job_ids: list[UUID], user_id: UUID) -> dict[UUID, list[JobPoolProvenance]]:
        if not job_ids:
            return {}
        rows = self.db.execute(
            select(
                JobProvenance, Source.name, Source.source_type, Source.operational_state,
                RawSourceRecord.external_record_id, RawSourceRecord.content_hash, RawSourceRecord.raw_content,
            )
            .join(Source, Source.id == JobProvenance.source_id)
            .outerjoin(RawSourceRecord, RawSourceRecord.id == JobProvenance.raw_source_record_id)
            .where(
                JobProvenance.job_id.in_(job_ids),
                or_(
                    public_source_visibility_predicate(),
                    and_(Source.source_type == "MANUAL_SUBMISSION",
                         JobProvenance.submitted_by_user_id == user_id),
                ),
            )
            .order_by(JobProvenance.observed_at.desc(), JobProvenance.id.desc())
        ).all()
        result: dict[UUID, list[JobPoolProvenance]] = {}
        seen: set[tuple[UUID, UUID]] = set()
        for provenance, source_name, source_type, operational_state, external_id, content_hash, raw_content in rows:
            key = (provenance.job_id, provenance.source_id)
            if key in seen:
                continue
            seen.add(key)
            posting_date = raw_content.get("posting_date") if isinstance(raw_content, dict) else None
            result.setdefault(provenance.job_id, []).append(JobPoolProvenance(
                source_id=provenance.source_id, source_name=source_name, source_type=source_type,
                source_operational_state=operational_state, source_url=provenance.source_url,
                external_job_id=external_id, content_hash=content_hash,
                posting_date=posting_date if isinstance(posting_date, str) else None,
                observed_at=provenance.observed_at, observation_type=provenance.observation_type,
                status=provenance.status,
            ))
        return result

    def _description_history(self, job_id: UUID):
        rows = self.db.scalars(
            select(JobDescriptionVersion).where(JobDescriptionVersion.job_id == job_id)
            .order_by(JobDescriptionVersion.version_number.desc(), JobDescriptionVersion.id.desc())
            .limit(21)
        ).all()
        truncated = len(rows) > 20
        summaries = [JobPoolDescriptionVersionSummary(
            version_number=row.version_number, content_hash=row.content_hash,
            completeness_status=row.completeness_status, retrieved_at=row.retrieved_at,
            requirement_extraction_status=row.requirement_extraction_status,
            requirement_extraction_attempts=row.requirement_extraction_attempts,
            requirement_extraction_error=row.requirement_extraction_error,
            requirements_extracted_at=row.requirements_extracted_at,
        ) for row in rows[:20]]
        return summaries, truncated

    def _requirements(self, description_version_id: UUID | None):
        if description_version_id is None:
            return [], False
        rows = self.db.scalars(
            select(JobRequirement).where(JobRequirement.job_description_version_id == description_version_id)
            .order_by(JobRequirement.dimension.asc().nullslast(), JobRequirement.id.asc()).limit(201)
        ).all()
        truncated = len(rows) > 200
        requirements = [JobPoolRequirement(
            id=row.id, requirement_type=row.requirement_type, description=row.description,
            importance=row.importance, severity=row.severity, dimension=row.dimension,
            hard_constraint=row.hard_constraint, extraction_status=row.extraction_status,
            source_excerpt=row.source_excerpt, extraction_confidence=row.extraction_confidence,
            extraction_version=row.extraction_version,
        ) for row in rows[:200]]
        return requirements, truncated

    def _verifications(self, job_ids: list[UUID]) -> dict[UUID, JobVerificationAttempt]:
        if not job_ids:
            return {}
        ranked = select(
            JobVerificationAttempt.id.label("verification_id"),
            func.row_number().over(
                partition_by=JobVerificationAttempt.job_id,
                order_by=(JobVerificationAttempt.checked_at.desc(), JobVerificationAttempt.id.desc()),
            ).label("row_number"),
        ).where(JobVerificationAttempt.job_id.in_(job_ids)).subquery()
        rows = self.db.scalars(
            select(JobVerificationAttempt)
            .join(ranked, ranked.c.verification_id == JobVerificationAttempt.id)
            .where(ranked.c.row_number == 1)
        ).all()
        return {row.job_id: row for row in rows}

    def _last_evaluated_at(self, user_id: UUID, job_ids: list[UUID]) -> dict[UUID, object]:
        if not job_ids:
            return {}
        profile_id = self.db.scalar(
            select(CandidateProfile.id).where(CandidateProfile.user_id == user_id).limit(1)
        )
        if profile_id is None:
            return {}
        rows = self.db.execute(
            select(CandidateEvaluation.job_id, func.max(CandidateEvaluation.evaluated_at))
            .where(CandidateEvaluation.candidate_profile_id == profile_id,
                   CandidateEvaluation.job_id.in_(job_ids))
            .group_by(CandidateEvaluation.job_id)
        ).all()
        return {job_id: evaluated_at for job_id, evaluated_at in rows}

    def _discovery_history(self, job_id: UUID, user_id: UUID):
        rows = self.db.execute(
            select(JobDiscoveryEvent, Source.name, Source.source_type, Source.operational_state)
            .join(Source, Source.id == JobDiscoveryEvent.source_id)
            .outerjoin(RawSourceRecord, RawSourceRecord.id == JobDiscoveryEvent.raw_source_record_id)
            .outerjoin(JobProvenance, and_(
                JobProvenance.raw_source_record_id == JobDiscoveryEvent.raw_source_record_id,
                JobProvenance.job_id == JobDiscoveryEvent.canonical_job_id,
            ))
            .where(
                JobDiscoveryEvent.canonical_job_id == job_id,
                or_(
                    public_source_visibility_predicate(),
                    and_(Source.source_type == "MANUAL_SUBMISSION",
                         JobProvenance.submitted_by_user_id == user_id),
                ),
            )
            .order_by(JobDiscoveryEvent.observed_at.desc(), JobDiscoveryEvent.id.desc())
            .limit(101)
        ).all()
        truncated = len(rows) > 100
        history, seen = [], set()
        for event, source_name, source_type, operational_state in rows[:100]:
            if event.id in seen:
                continue
            seen.add(event.id)
            history.append(JobPoolDiscoveryEvent(
                event_id=event.id, source_id=event.source_id, source_name=source_name,
                source_type=source_type, source_operational_state=operational_state,
                source_url=event.source_url, external_job_id=event.external_record_id,
                content_hash=event.content_hash, observed_at=event.observed_at,
                observation_outcome=event.observation_outcome,
                processing_status=event.processing_status, processing_error=event.processing_error,
            ))
        return history, truncated

    @staticmethod
    def _description_payload(row: JobDescriptionVersion | None, *, detail: bool):
        if row is None:
            return None
        common = dict(
            version_number=row.version_number, preview=row.content[:320], content_hash=row.content_hash,
            completeness_status=row.completeness_status, retrieved_at=row.retrieved_at,
            requirement_extraction_status=row.requirement_extraction_status,
            requirement_extraction_attempts=row.requirement_extraction_attempts,
            requirement_extraction_error=row.requirement_extraction_error,
            requirements_extracted_at=row.requirements_extracted_at,
        )
        if detail:
            common["content"] = row.content
            return JobPoolDescription(**common)
        return JobPoolDescriptionPreview(**common)

    def _item(
        self, job: Job, description: JobDescriptionVersion | None,
        provenance: list[JobPoolProvenance], *, detail: bool = False,
        verification: JobVerificationAttempt | None = None, last_evaluated_at=None,
        description_versions=None, description_versions_truncated=False,
        requirements=None, requirements_truncated=False, discovery_history=None,
        discovery_history_truncated=False,
    ):
        metadata = job.metadata_json if isinstance(job.metadata_json, dict) else {}
        posting_date = next(
            (p.posting_date for p in provenance if p.source_type != "MANUAL_SUBMISSION" and p.posting_date), None
        ) or next((p.posting_date for p in provenance if p.posting_date), None)
        base = dict(
            id=job.id, title=job.canonical_title, company_name=job.company_name,
            seniority=metadata.get("seniority") if isinstance(metadata.get("seniority"), str) else None,
            employment_type=job.employment_type,
            salary_min=float(job.salary_min) if job.salary_min is not None else None,
            salary_max=float(job.salary_max) if job.salary_max is not None else None,
            salary_currency=job.salary_currency, salary_period=job.salary_period,
            location_country=job.location_country, location_region=job.location_region,
            location_city=job.location_city, original_location_text=job.original_location_text,
            work_mode=job.work_mode, remote_eligibility=job.remote_eligibility,
            location_restrictions=job.location_restrictions or {}, application_url=job.application_url,
            posting_date=posting_date,
            identity_fingerprint=metadata.get("identity_fingerprint") if isinstance(metadata.get("identity_fingerprint"), str) else None,
            jd_retrieval_status=metadata.get("jd_retrieval_status") if isinstance(metadata.get("jd_retrieval_status"), str) else None,
            verification_status=verification.status if verification else None,
            first_discovered_at=job.first_discovered_at, last_observed_at=job.last_observed_at,
            last_jd_retrieved_at=description.retrieved_at if description else None,
            last_verified_at=job.last_verified_at, last_evaluated_at=last_evaluated_at,
            description=self._description_payload(description, detail=detail), provenance=provenance,
        )
        if not detail:
            return JobPoolItem(**base)
        base.update(
            verification=JobPoolVerification(
                status=verification.status, http_status_code=verification.http_status_code,
                reason_code=verification.reason_code, verifier_version=verification.verifier_version,
                checked_at=verification.checked_at,
            ) if verification else None,
            description_versions=description_versions or [],
            description_versions_truncated=description_versions_truncated,
            requirements=requirements or [],
            requirements_truncated=requirements_truncated,
            responsibilities=[
                req.description for req in (requirements or [])
                if req.requirement_type.strip().upper() == "RESPONSIBILITY"
            ],
            discovery_history=discovery_history or [],
            discovery_history_truncated=discovery_history_truncated,
        )
        return JobPoolDetail(**base)

    def list_jobs(
        self, user_id: UUID, *, limit: int = 20, offset: int = 0, query: str | None = None,
        lifecycle_status: str | None = None, freshness_status: str | None = None,
        location_country: str | None = None, work_mode: str | None = None,
    ) -> dict:
        if limit < 1 or limit > 50 or offset < 0:
            raise DomainError("INVALID_PAGINATION", "limit must be 1–50 and offset must be non-negative", 422)
        filters = [Job.identity_status == "CANONICAL", candidate_job_visibility_predicate(user_id)]
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
            base.order_by(Job.last_observed_at.desc(), Job.id.desc()).offset(offset).limit(limit)
        ).all())
        ids = [job.id for job in jobs]
        descriptions, provenance = self._descriptions(ids), self._provenance(ids, user_id)
        verifications, evaluated = self._verifications(ids), self._last_evaluated_at(user_id, ids)
        return {
            "data": [self._item(job, descriptions.get(job.id), provenance.get(job.id, []),
                                verification=verifications.get(job.id), last_evaluated_at=evaluated.get(job.id))
                     for job in jobs],
            "pagination": JobPoolPagination(limit=limit, offset=offset, total=total),
        }

    def get_job(self, user_id: UUID, job_id: UUID) -> JobPoolDetail:
        if not candidate_can_access_job(self.db, user_id, job_id):
            raise DomainError("JOB_NOT_FOUND", "Job not found", 404)
        job = self.db.scalar(select(Job).where(Job.id == job_id, Job.identity_status == "CANONICAL"))
        if job is None:
            raise DomainError("JOB_NOT_FOUND", "Job not found", 404)
        description = self._descriptions([job.id]).get(job.id)
        provenance = self._provenance([job.id], user_id).get(job.id, [])
        verification = self._verifications([job.id]).get(job.id)
        evaluated = self._last_evaluated_at(user_id, [job.id]).get(job.id)
        versions, versions_truncated = self._description_history(job.id)
        requirements, requirements_truncated = self._requirements(description.id if description else None)
        history, history_truncated = self._discovery_history(job.id, user_id)
        return self._item(
            job, description, provenance, detail=True, verification=verification,
            last_evaluated_at=evaluated, description_versions=versions,
            description_versions_truncated=versions_truncated, requirements=requirements,
            requirements_truncated=requirements_truncated, discovery_history=history,
            discovery_history_truncated=history_truncated,
        )
