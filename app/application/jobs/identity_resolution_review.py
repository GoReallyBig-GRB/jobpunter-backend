"""Authorized administrative review of ambiguous canonical job identity."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.jobs.normalization import normalize_application_route_type, normalize_raw_record, normalize_url
from app.application.jobs.source_governance import has_approved_permission_review
from app.infrastructure.db.models import (
    AcquisitionRun,
    AdminAuditEvent,
    Job,
    JobDescriptionVersion,
    JobDiscoveryEvent,
    JobProvenance,
    JobResolution,
    JobResolutionCandidate,
    RawSourceRecord,
    Source,
    SourceConnector,
    SourceConnectorConfigurationVersion,
)


class JobIdentityResolutionReviewService:
    def __init__(self, db: Session):
        self.db = db

    def _candidates(self, resolution_id: UUID) -> list[dict]:
        rows = self.db.execute(
            select(JobResolutionCandidate, Job)
            .join(Job, Job.id == JobResolutionCandidate.candidate_job_id)
            .where(JobResolutionCandidate.resolution_id == resolution_id)
            .order_by(
                JobResolutionCandidate.confidence.desc(),
                JobResolutionCandidate.candidate_job_id.asc(),
            )
        ).all()
        return [
            {
                "job_id": job.id,
                "title": job.canonical_title,
                "company_name": job.company_name,
                "location_city": job.location_city,
                "location_country": job.location_country,
                "application_url": job.application_url,
                "identity_status": job.identity_status,
                "lifecycle_status": job.lifecycle_status,
                "confidence": candidate.confidence,
                "method": candidate.method,
            }
            for candidate, job in rows
        ]

    def _summary(self, resolution: JobResolution) -> dict:
        raw = self.db.get(RawSourceRecord, resolution.raw_source_record_id)
        source = self.db.get(Source, raw.source_id) if raw else None
        content = raw.raw_content if raw and isinstance(raw.raw_content, dict) else {}
        normalized = normalize_raw_record(content)
        return {
            "resolution_id": resolution.id,
            "raw_source_record_id": resolution.raw_source_record_id,
            "source_id": raw.source_id if raw else None,
            "source_name": source.name if source else None,
            "source_type": source.source_type if source else None,
            "source_url": raw.source_url if raw else None,
            "external_record_id": raw.external_record_id if raw else None,
            "content_hash": raw.content_hash if raw else None,
            "acquired_at": raw.acquired_at if raw else None,
            "title": normalized["canonical_title"],
            "company_name": normalized["company_name"],
            "location": normalized["original_location_text"],
            "application_url": normalized["application_url"],
            "resolution_method": resolution.resolution_method,
            "confidence": resolution.confidence,
            "status": resolution.status,
            "created_at": resolution.created_at,
            "candidates": self._candidates(resolution.id),
        }

    def list_pending(self, *, limit: int, offset: int) -> dict:
        if limit < 1 or limit > 100 or offset < 0:
            raise DomainError("INVALID_PAGINATION", "limit must be 1–100 and offset must be non-negative", 422)
        filters = (
            JobResolution.status == "REQUIRES_REVIEW",
            JobResolution.superseded_at.is_(None),
        )
        total = int(self.db.scalar(
            select(func.count(JobResolution.id)).where(*filters)
        ) or 0)
        rows = self.db.scalars(
            select(JobResolution)
            .where(*filters)
            .order_by(JobResolution.created_at.asc(), JobResolution.id.asc())
            .offset(offset)
            .limit(limit)
        ).all()
        return {
            "data": [self._summary(row) for row in rows],
            "pagination": {"limit": limit, "offset": offset, "total": total},
        }

    def get_detail(self, resolution_id: UUID) -> dict:
        resolution = self.db.get(JobResolution, resolution_id)
        if resolution is None:
            raise DomainError("JOB_RESOLUTION_NOT_FOUND", "Job resolution not found", 404)
        raw = self.db.get(RawSourceRecord, resolution.raw_source_record_id)
        source = self.db.get(Source, raw.source_id) if raw else None
        if raw is None or source is None:
            raise DomainError("JOB_RESOLUTION_SOURCE_NOT_FOUND", "Resolution source record not found", 404)
        data = self._summary(resolution)
        data.update({
            "resolution_type": resolution.resolution_type,
            "resolver_version": resolution.resolver_version,
            "resolved_at": resolution.resolved_at,
            "superseded_at": resolution.superseded_at,
            "related_resolution_id": resolution.related_resolution_id,
            "raw_record": {
                "id": raw.id,
                "source_id": raw.source_id,
                "source_url": raw.source_url,
                "external_record_id": raw.external_record_id,
                "content_hash": raw.content_hash,
                "status": raw.status,
                "acquired_at": raw.acquired_at,
                "raw_content": raw.raw_content if isinstance(raw.raw_content, dict) else {},
            },
        })
        return data

    @staticmethod
    def _manual_owner(raw: RawSourceRecord) -> UUID | None:
        value = raw.raw_content.get("submitted_by_user_id") if isinstance(raw.raw_content, dict) else None
        try:
            return UUID(str(value)) if value else None
        except (TypeError, ValueError):
            return None

    def _manual_job_is_accessible(self, job_id: UUID, owner_id: UUID | None) -> bool:
        if owner_id is None:
            return False
        has_public_provenance = self.db.scalar(
            select(JobProvenance.id)
            .join(Source, Source.id == JobProvenance.source_id)
            .where(
                JobProvenance.job_id == job_id,
                Source.source_type != "MANUAL_SUBMISSION",
                Source.status == "ACTIVE",
                Source.permission_review_status == "APPROVED",
                Source.permission_review_reference.is_not(None),
                Source.permission_reviewed_at.is_not(None),
            )
            .limit(1)
        )
        if has_public_provenance is not None:
            return True
        has_other_manual_owner = self.db.scalar(
            select(JobProvenance.id).where(
                JobProvenance.job_id == job_id,
                JobProvenance.submitted_by_user_id.is_not(None),
                JobProvenance.submitted_by_user_id != owner_id,
            ).limit(1)
        )
        if has_other_manual_owner is not None:
            return False
        has_owner_provenance = self.db.scalar(
            select(JobProvenance.id).where(
                JobProvenance.job_id == job_id,
                JobProvenance.submitted_by_user_id == owner_id,
            ).limit(1)
        )
        has_non_manual = self.db.scalar(
            select(JobProvenance.id)
            .join(Source, Source.id == JobProvenance.source_id)
            .where(JobProvenance.job_id == job_id, Source.source_type != "MANUAL_SUBMISSION")
            .limit(1)
        )
        return has_owner_provenance is not None and has_non_manual is None

    def _manual_job_is_owned_private(self, job_id: UUID, owner_id: UUID | None) -> bool:
        if owner_id is None:
            return False
        has_owner_provenance = self.db.scalar(
            select(JobProvenance.id).where(
                JobProvenance.job_id == job_id,
                JobProvenance.submitted_by_user_id == owner_id,
            ).limit(1)
        )
        has_other_owner = self.db.scalar(
            select(JobProvenance.id).where(
                JobProvenance.job_id == job_id,
                JobProvenance.submitted_by_user_id.is_not(None),
                JobProvenance.submitted_by_user_id != owner_id,
            ).limit(1)
        )
        has_non_manual_provenance = self.db.scalar(
            select(JobProvenance.id)
            .join(Source, Source.id == JobProvenance.source_id)
            .where(JobProvenance.job_id == job_id, Source.source_type != "MANUAL_SUBMISSION")
            .limit(1)
        )
        return (
            has_owner_provenance is not None
            and has_other_owner is None
            and has_non_manual_provenance is None
        )

    def _explicit_route_type(self, raw: RawSourceRecord, source: Source, normalized: dict) -> None:
        if normalized.get("application_route_type") is not None:
            return
        run = self.db.get(AcquisitionRun, raw.acquisition_run_id)
        connector = self.db.get(SourceConnector, run.source_connector_id) if run else None
        version = (
            self.db.get(SourceConnectorConfigurationVersion, run.configuration_version_id)
            if run is not None and run.configuration_version_id is not None
            else None
        )
        configs = (
            version.configuration if version is not None else connector.configuration if connector else None,
            source.configuration,
        )
        for config in configs:
            if not isinstance(config, dict):
                continue
            route_type = normalize_application_route_type(
                config.get("application_route_type") or config.get("application_route_classification")
            )
            if route_type is not None:
                normalized["application_route_type"] = route_type
                return

    def resolve(
        self,
        *,
        resolution_id: UUID,
        action: str,
        job_id: UUID | None,
        reason: str,
        actor_user_id: UUID,
        request_id: str | None,
    ) -> dict:
        resolution = self.db.scalar(
            select(JobResolution)
            .where(JobResolution.id == resolution_id)
            .with_for_update()
        )
        if resolution is None:
            raise DomainError("JOB_RESOLUTION_NOT_FOUND", "Job resolution not found", 404)
        if resolution.status != "REQUIRES_REVIEW" or resolution.superseded_at is not None:
            raise DomainError("JOB_RESOLUTION_ALREADY_DECIDED", "This identity resolution is no longer pending review", 409)

        raw = self.db.scalar(
            select(RawSourceRecord)
            .where(RawSourceRecord.id == resolution.raw_source_record_id)
            .with_for_update()
        )
        if raw is None:
            raise DomainError("JOB_RESOLUTION_SOURCE_NOT_FOUND", "Resolution source record not found", 404)
        source = self.db.get(Source, raw.source_id)
        if source is None:
            raise DomainError("JOB_RESOLUTION_SOURCE_NOT_FOUND", "Resolution source record not found", 404)
        if source.source_type != "MANUAL_SUBMISSION" and not has_approved_permission_review(source):
            raise DomainError(
                "SOURCE_PERMISSION_REVIEW_REQUIRED",
                "Identity resolution is blocked until the source permission review is approved.",
                409,
            )

        normalized = normalize_raw_record(raw.raw_content if isinstance(raw.raw_content, dict) else {})
        now = datetime.now(timezone.utc)
        before = {
            "status": resolution.status,
            "job_id": str(resolution.job_id) if resolution.job_id else None,
            "candidate_job_ids": [
                str(row.candidate_job_id)
                for row in self.db.scalars(
                    select(JobResolutionCandidate)
                    .where(JobResolutionCandidate.resolution_id == resolution.id)
                    .order_by(JobResolutionCandidate.candidate_job_id)
                ).all()
            ],
        }

        if action == "MATCH_EXISTING":
            if job_id is None:
                raise DomainError("JOB_RESOLUTION_TARGET_REQUIRED", "A candidate job must be selected", 422)
            candidate = self.db.scalar(
                select(JobResolutionCandidate).where(
                    JobResolutionCandidate.resolution_id == resolution.id,
                    JobResolutionCandidate.candidate_job_id == job_id,
                )
            )
            if candidate is None:
                raise DomainError(
                    "JOB_RESOLUTION_TARGET_NOT_CANDIDATE",
                    "The selected job is not a candidate for this resolution.",
                    422,
                )
            job = self.db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is None or job.identity_status != "CANONICAL":
                raise DomainError("JOB_RESOLUTION_TARGET_NOT_CANONICAL", "The selected job is no longer canonical", 409)
            if source.source_type == "MANUAL_SUBMISSION" and not self._manual_job_is_accessible(
                job.id, self._manual_owner(raw)
            ):
                raise DomainError(
                    "JOB_RESOLUTION_TARGET_NOT_ACCESSIBLE",
                    "The selected job is not accessible to the manual submission owner.",
                    409,
                )
            resolution_status = "MATCHED"
            resolution_method = "ADMIN_MATCH_EXISTING"
            confidence = 1.0
        elif action == "CREATE_NEW":
            if job_id is not None:
                raise DomainError("JOB_RESOLUTION_TARGET_NOT_ALLOWED", "Do not supply job_id when creating a new job", 422)
            if not normalized["canonical_title"]:
                raise DomainError("JOB_RESOLUTION_TITLE_REQUIRED", "A usable job title is required to create a canonical job", 422)
            manual_owner_id = self._manual_owner(raw) if source.source_type == "MANUAL_SUBMISSION" else None
            if source.source_type == "MANUAL_SUBMISSION" and manual_owner_id is None:
                raise DomainError("MANUAL_SUBMISSION_OWNER_REQUIRED", "A valid submitter is required to create a private manual job", 422)
            self._explicit_route_type(raw, source, normalized)
            metadata = {"identity_fingerprint": normalized["identity_fingerprint"]}
            if normalized.get("seniority") is not None:
                metadata["seniority"] = normalized["seniority"]
            for key in ("external_job_id", "posting_date", "discovery_method", "submitted_at", "detected_email_source", "email_id"):
                if isinstance(raw.raw_content, dict) and raw.raw_content.get(key) is not None:
                    metadata[key] = raw.raw_content[key]
            if normalized.get("application_fee_amount") is not None:
                metadata["application_fee_amount"] = normalized["application_fee_amount"]
            if normalized.get("application_fee_currency") is not None:
                metadata["application_fee_currency"] = normalized["application_fee_currency"]
            if normalized.get("relocation_required") is not None:
                metadata["relocation_required"] = normalized["relocation_required"]
            if normalized.get("application_route_type") is not None:
                metadata["application_route_type"] = normalized["application_route_type"]
            job = Job(
                identity_status="CANONICAL",
                lifecycle_status="DISCOVERED",
                freshness_status="UNKNOWN",
                canonical_title=normalized["canonical_title"],
                company_name=normalized["company_name"],
                employment_type=normalized["employment_type"],
                salary_min=normalized["salary_min"],
                salary_max=normalized["salary_max"],
                salary_currency=normalized["salary_currency"],
                salary_period=normalized["salary_period"],
                salary_type=normalized["salary_type"],
                original_salary_text=normalized["original_salary_text"],
                location_country=normalized["location_country"],
                location_region=normalized["location_region"],
                location_city=normalized["location_city"],
                work_mode=normalized["work_mode"],
                remote_eligibility=normalized["remote_eligibility"],
                original_location_text=normalized["original_location_text"],
                application_url=normalized["application_url"],
                location_restrictions={},
                first_discovered_at=raw.acquired_at,
                last_observed_at=raw.acquired_at,
                metadata_json=metadata,
            )
            self.db.add(job)
            self.db.flush()
            if normalized["description"]:
                self.db.add(JobDescriptionVersion(
                    job_id=job.id,
                    version_number=1,
                    content=normalized["description"],
                    content_hash=normalized["description_hash"],
                    source_record_id=raw.id,
                    completeness_status=normalized["description_completeness"],
                    retrieved_at=raw.acquired_at,
                ))
            resolution_status = "CREATED"
            resolution_method = "ADMIN_CREATE_NEW"
            confidence = 1.0
        else:
            raise DomainError("INVALID_JOB_RESOLUTION_ACTION", "Unsupported identity-resolution action", 422)

        if action == "MATCH_EXISTING":
            manual_owner_id = self._manual_owner(raw) if source.source_type == "MANUAL_SUBMISSION" else None
            may_update_canonical_content = (
                source.source_type != "MANUAL_SUBMISSION"
                or self._manual_job_is_owned_private(job.id, manual_owner_id)
            )
            if may_update_canonical_content:
                metadata = dict(job.metadata_json or {})
                metadata["identity_fingerprint"] = normalized["identity_fingerprint"]
                if normalized.get("seniority") is not None:
                    metadata["seniority"] = normalized["seniority"]
                for key in (
                    "external_job_id", "posting_date", "discovery_method", "submitted_at",
                    "detected_email_source", "email_id",
                ):
                    if isinstance(raw.raw_content, dict) and raw.raw_content.get(key) is not None:
                        metadata[key] = raw.raw_content[key]
                for key in ("application_fee_amount", "application_fee_currency", "relocation_required"):
                    if normalized.get(key) is not None:
                        metadata[key] = normalized[key]
                if (
                    normalized.get("application_route_type") is not None
                    and normalized.get("application_url")
                    and normalized["application_url"] == job.application_url
                ):
                    metadata["application_route_type"] = normalized["application_route_type"]
                job.metadata_json = metadata

                if (
                    normalized.get("salary_currency")
                    and normalized.get("salary_period")
                    and (
                        normalized.get("salary_min") is not None
                        or normalized.get("salary_max") is not None
                    )
                ):
                    job.salary_min = normalized["salary_min"]
                    job.salary_max = normalized["salary_max"]
                    job.salary_currency = normalized["salary_currency"]
                    job.salary_period = normalized["salary_period"]
                    if normalized.get("salary_type") is not None:
                        job.salary_type = normalized["salary_type"]
                    if normalized.get("original_salary_text") is not None:
                        job.original_salary_text = normalized["original_salary_text"]

                if normalized["description"]:
                    current_description = self.db.scalar(
                        select(JobDescriptionVersion)
                        .where(JobDescriptionVersion.job_id == job.id)
                        .order_by(JobDescriptionVersion.version_number.desc())
                        .limit(1)
                    )
                    if (
                        current_description is None
                        or current_description.content_hash != normalized["description_hash"]
                    ):
                        self.db.add(JobDescriptionVersion(
                            job_id=job.id,
                            version_number=1 if current_description is None else current_description.version_number + 1,
                            content=normalized["description"],
                            content_hash=normalized["description_hash"],
                            source_record_id=raw.id,
                            completeness_status=normalized["description_completeness"],
                            retrieved_at=raw.acquired_at,
                        ))

        if self.db.scalar(
            select(JobProvenance.id).where(JobProvenance.raw_source_record_id == raw.id).limit(1)
        ) is not None:
            raise DomainError("JOB_RESOLUTION_PROVENANCE_EXISTS", "This raw record already has provenance", 409)

        resolution.superseded_at = now
        resolved = JobResolution(
            raw_source_record_id=raw.id,
            job_id=job.id,
            resolution_type="RESOLVE",
            resolution_method=resolution_method,
            confidence=confidence,
            resolver_version="admin-review-v1",
            status=resolution_status,
            resolved_at=now,
            related_resolution_id=resolution.id,
            created_at=now,
        )
        self.db.add(resolved)
        self.db.flush()

        manual_owner_id = self._manual_owner(raw) if source.source_type == "MANUAL_SUBMISSION" else None
        self.db.add(JobProvenance(
            job_id=job.id,
            source_id=source.id,
            raw_source_record_id=raw.id,
            submitted_by_user_id=manual_owner_id,
            source_url=raw.source_url,
            normalized_source_url=normalize_url(raw.source_url),
            observation_type="ACQUIRED",
            asserted_by="SOURCE",
            observed_at=raw.acquired_at,
            confidence=confidence,
            status="OBSERVED",
            metadata_json={
                "identity_fingerprint": normalized["identity_fingerprint"],
                "description_hash": normalized["description_hash"],
                "identity_resolution_reason": reason,
            },
        ))

        events = self.db.scalars(
            select(JobDiscoveryEvent)
            .where(JobDiscoveryEvent.raw_source_record_id == raw.id)
            .with_for_update()
        ).all()
        for event in events:
            event.canonical_job_id = job.id
            event.processing_status = "CANONICALIZED"
            event.processing_error = None
        if events and source.source_type != "MANUAL_SUBMISSION":
            latest_observed = max(event.observed_at for event in events)
            observed_at = latest_observed.replace(tzinfo=timezone.utc) if latest_observed.tzinfo is None else latest_observed
            last_observed = job.last_observed_at
            last_observed = last_observed.replace(tzinfo=timezone.utc) if last_observed.tzinfo is None else last_observed
            if observed_at > last_observed:
                job.last_observed_at = observed_at
                job.updated_at = now
        elif source.source_type != "MANUAL_SUBMISSION" and raw.acquired_at > job.last_observed_at:
            job.last_observed_at = raw.acquired_at
            job.updated_at = now

        self.db.add(AdminAuditEvent(
            actor_user_id=actor_user_id,
            action="JOB_IDENTITY_RESOLUTION_DECIDED",
            resource_type="job_resolution",
            resource_id=resolved.id,
            outcome="SUCCESS",
            details={
                "raw_source_record_id": str(raw.id),
                "superseded_resolution_id": str(resolution.id),
                "before": before,
                "after": {
                    "resolution_id": str(resolved.id),
                    "status": resolved.status,
                    "job_id": str(job.id),
                    "resolution_method": resolved.resolution_method,
                },
                "reason": reason,
            },
            request_id=request_id,
            created_at=now,
        ))
        self.db.commit()
        return {
            "resolution_id": resolved.id,
            "superseded_resolution_id": resolution.id,
            "raw_source_record_id": raw.id,
            "job_id": job.id,
            "status": resolved.status,
            "resolution_method": resolved.resolution_method,
            "reason": reason,
            "resolved_at": now,
        }
