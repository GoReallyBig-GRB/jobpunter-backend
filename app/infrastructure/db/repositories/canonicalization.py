from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.jobs.identity import IdentityCandidate, decide_identity
from app.application.jobs.normalization import normalize_raw_record
from app.infrastructure.db.models import Job, JobDescriptionVersion, JobProvenance, JobResolution, RawSourceRecord, Source


class SqlAlchemyCanonicalizationRepository:
    """Transactional raw-record-to-canonical-job persistence for PostgreSQL."""

    def __init__(self, db: Session):
        self.db = db

    def process_raw_record(self, raw_record_id: UUID) -> dict:
        raw = self.db.scalar(
            select(RawSourceRecord).where(RawSourceRecord.id == raw_record_id).with_for_update()
        )
        if raw is None:
            raise ValueError("Raw source record not found")

        existing = self.db.scalar(
            select(JobResolution)
            .where(JobResolution.raw_source_record_id == raw_record_id)
            .order_by(JobResolution.created_at.desc())
        )
        if existing is not None:
            self.db.rollback()
            return {"raw_source_record_id": str(raw_record_id), "job_id": str(existing.job_id),
                    "status": existing.status, "replayed": True}

        source = self.db.get(Source, raw.source_id)
        if source is None:
            raise ValueError("Source for raw record not found")
        normalized = normalize_raw_record(raw.raw_content)
        if not normalized["canonical_title"]:
            raise ValueError("Raw source record has no usable job title")

        candidates_by_id = {}
        if normalized["application_url"]:
            rows = self.db.scalars(select(Job).where(
                Job.application_url == normalized["application_url"],
                Job.identity_status == "CANONICAL",
            )).all()
            for job in rows:
                candidates_by_id[job.id] = (job, "EXACT_APPLICATION_URL")

        fingerprint = normalized["identity_fingerprint"]
        rows = self.db.scalars(select(Job).where(
            Job.metadata_json["identity_fingerprint"].as_string() == fingerprint,
            Job.identity_status == "CANONICAL",
        )).all()
        for job in rows:
            candidates_by_id.setdefault(job.id, (job, "EXACT_NORMALIZED_FINGERPRINT"))

        candidates = [IdentityCandidate(str(job_id), 1.0, method)
                      for job_id, (_, method) in candidates_by_id.items()]
        decision = decide_identity(candidates)
        now = datetime.now(timezone.utc)

        if decision.status.value == "NEW":
            job = Job(
                identity_status="CANONICAL", lifecycle_status="DISCOVERED",
                freshness_status="UNKNOWN", canonical_title=normalized["canonical_title"],
                company_name=normalized["company_name"], employment_type=normalized["employment_type"],
                location_country=normalized["location_country"], location_region=normalized["location_region"],
                location_city=normalized["location_city"], work_mode=normalized["work_mode"],
                remote_eligibility=normalized["remote_eligibility"],
                original_location_text=normalized["original_location_text"],
                application_url=normalized["application_url"], location_restrictions={},
                first_discovered_at=raw.acquired_at, last_observed_at=raw.acquired_at,
                metadata_json={"identity_fingerprint": fingerprint},
            )
            self.db.add(job)
            self.db.flush()
            resolution_status, resolution_method, confidence = "CREATED", "NO_EXACT_MATCH", 1.0
        else:
            selected_id = UUID(decision.job_id) if decision.job_id else UUID(decision.candidate_job_ids[0])
            job = candidates_by_id[selected_id][0]
            job.last_observed_at = max(job.last_observed_at, raw.acquired_at)
            resolution_status = "MATCHED" if decision.status.value == "MATCHED" else "REQUIRES_REVIEW"
            resolution_method, confidence = decision.method, decision.confidence

        description = normalized["description"]
        if description:
            current = self.db.scalar(select(JobDescriptionVersion)
                .where(JobDescriptionVersion.job_id == job.id)
                .order_by(JobDescriptionVersion.version_number.desc()).limit(1))
            if current is None or current.content_hash != normalized["description_hash"]:
                self.db.add(JobDescriptionVersion(
                    job_id=job.id, version_number=1 if current is None else current.version_number + 1,
                    content=description, content_hash=normalized["description_hash"],
                    source_record_id=raw.id, completeness_status=normalized["description_completeness"],
                    retrieved_at=raw.acquired_at,
                ))

        self.db.add(JobResolution(
            raw_source_record_id=raw.id, job_id=job.id, resolution_type="RESOLVE",
            resolution_method=resolution_method, confidence=confidence,
            resolver_version="exact-identity-v1", status=resolution_status, resolved_at=now,
        ))
        self.db.add(JobProvenance(
            job_id=job.id, source_id=raw.source_id, raw_source_record_id=raw.id,
            source_url=raw.source_url, observation_type="ACQUIRED", asserted_by="SOURCE",
            observed_at=raw.acquired_at, confidence=confidence, status="OBSERVED",
            metadata_json={"identity_fingerprint": fingerprint,
                           "description_hash": normalized["description_hash"]},
        ))
        self.db.commit()
        return {"raw_source_record_id": str(raw_record_id), "job_id": str(job.id),
                "status": resolution_status, "replayed": False}
