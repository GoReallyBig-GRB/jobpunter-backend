from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.application.jobs.identity import IdentityCandidate, IdentityDecision, ResolutionStatus, decide_identity
from app.application.jobs.normalization import normalize_raw_record
from app.infrastructure.db.models import Job, JobDescriptionVersion, JobProvenance, JobResolution, JobResolutionCandidate, RawSourceRecord, Source


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
            return {"raw_source_record_id": str(raw_record_id), "job_id": str(existing.job_id) if existing.job_id else None,
                    "status": existing.status, "replayed": True}

        source = self.db.get(Source, raw.source_id)
        if source is None:
            raise ValueError("Source for raw record not found")
        normalized = normalize_raw_record(raw.raw_content)
        if not normalized["canonical_title"]:
            raise ValueError("Raw source record has no usable job title")

        # Serialize resolution for any shared exact-identity signal. Acquire every
        # applicable transaction-scoped advisory lock in sorted order to avoid
        # deadlocks when concurrent records overlap on multiple signals. The lock
        # prevents two transactions from both observing "no match" and creating
        # separate canonical jobs; identity matching still decides whether to merge.
        identity_lock_keys = []
        if raw.external_record_id:
            identity_lock_keys.append(
                f"source-external-id:{raw.source_id}:{raw.external_record_id}"
            )
        if normalized["application_url"]:
            identity_lock_keys.append(f"application-url:{normalized['application_url']}")
        identity_lock_keys.append(
            f"normalized-fingerprint:{normalized['identity_fingerprint']}"
        )
        for lock_key in sorted(set(identity_lock_keys)):
            self.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )

        candidates_by_id = {}
        # External identifiers are only meaningful within their originating source.
        # Resolve them through prior raw-record resolutions so they remain a signal,
        # not a second canonical-job store.
        if raw.external_record_id:
            rows = self.db.scalars(
                select(Job)
                .join(JobResolution, JobResolution.job_id == Job.id)
                .join(RawSourceRecord, RawSourceRecord.id == JobResolution.raw_source_record_id)
                .where(
                    RawSourceRecord.source_id == raw.source_id,
                    RawSourceRecord.external_record_id == raw.external_record_id,
                    Job.identity_status == "CANONICAL",
                    JobResolution.job_id.is_not(None),
                )
            ).all()
            for job in rows:
                candidates_by_id[job.id] = (job, "SOURCE_EXTERNAL_ID")

        url_collision_job_ids = set()
        if normalized["application_url"]:
            rows = self.db.scalars(select(Job).where(
                Job.application_url == normalized["application_url"],
                Job.identity_status == "CANONICAL",
            )).all()
            incoming_title = " ".join(str(normalized["canonical_title"] or "").casefold().split())
            incoming_company = " ".join(str(normalized["company_name"] or "").casefold().split())
            for job in rows:
                prior_method = candidates_by_id.get(job.id, (None, None))[1]
                candidates_by_id.setdefault(job.id, (job, "EXACT_APPLICATION_URL"))
                # A stable source-scoped external ID can legitimately survive a
                # title/URL change. A URL-only match cannot safely override
                # conflicting vacancy identity fields, because career landing
                # pages and reused URLs may point to different vacancies.
                if prior_method == "SOURCE_EXTERNAL_ID":
                    continue
                existing_title = " ".join(str(job.canonical_title or "").casefold().split())
                existing_company = " ".join(str(job.company_name or "").casefold().split())
                title_conflicts = bool(incoming_title and existing_title and incoming_title != existing_title)
                company_conflicts = bool(incoming_company and existing_company and incoming_company != existing_company)
                if title_conflicts or company_conflicts:
                    url_collision_job_ids.add(job.id)

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
        if url_collision_job_ids:
            # Preserve the conflicting canonical candidate(s) for explicit review;
            # never silently merge a different vacancy on URL equality alone.
            decision = IdentityDecision(
                ResolutionStatus.REQUIRES_REVIEW,
                None,
                "URL_IDENTITY_COLLISION",
                1.0,
                tuple(sorted(str(job_id) for job_id in candidates_by_id)),
            )
        now = datetime.now(timezone.utc)

        if decision.status.value == "REQUIRES_REVIEW":
            resolution = JobResolution(
                raw_source_record_id=raw.id,
                job_id=None,
                resolution_type="RESOLVE",
                resolution_method=decision.method,
                confidence=decision.confidence,
                resolver_version="exact-identity-v1",
                status="REQUIRES_REVIEW",
                resolved_at=now,
            )
            self.db.add(resolution)
            self.db.flush()
            confidence_by_id = {candidate.job_id: candidate.confidence for candidate in candidates}
            method_by_id = {candidate.job_id: candidate.method for candidate in candidates}
            for candidate_job_id in decision.candidate_job_ids:
                self.db.add(JobResolutionCandidate(
                    resolution_id=resolution.id,
                    candidate_job_id=UUID(candidate_job_id),
                    confidence=confidence_by_id[candidate_job_id],
                    method=method_by_id[candidate_job_id],
                ))
            self.db.commit()
            return {
                "raw_source_record_id": str(raw_record_id),
                "job_id": None,
                "status": "REQUIRES_REVIEW",
                "candidate_job_ids": list(decision.candidate_job_ids),
                "replayed": False,
            }

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

        # Retain channel-specific metadata without inventing fields absent from the submission.
        metadata = dict(job.metadata_json or {})
        metadata["identity_fingerprint"] = fingerprint
        if raw.raw_content.get("external_job_id"):
            metadata["external_job_id"] = raw.raw_content["external_job_id"]
        if raw.raw_content.get("posting_date"):
            metadata["posting_date"] = raw.raw_content["posting_date"]
        if raw.raw_content.get("discovery_method"):
            metadata["discovery_method"] = raw.raw_content["discovery_method"]
        if raw.raw_content.get("submitted_at"):
            metadata["submitted_at"] = raw.raw_content["submitted_at"]
        if raw.raw_content.get("detected_email_source"):
            metadata["detected_email_source"] = raw.raw_content["detected_email_source"]
        if raw.raw_content.get("email_id"):
            metadata["email_id"] = raw.raw_content["email_id"]
        job.metadata_json = metadata

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
