from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.application.jobs.identity import IdentityCandidate, IdentityDecision, ResolutionStatus, decide_identity
from app.application.jobs.normalization import normalize_application_route_type, normalize_raw_record, normalize_url
from app.infrastructure.db.models import AcquisitionRun, Job, JobDescriptionVersion, JobDiscoveryEvent, JobProvenance, JobResolution, JobResolutionCandidate, RawSourceRecord, Source, SourceConnector, SourceConnectorConfigurationVersion


class SqlAlchemyCanonicalizationRepository:
    """Transactional raw-record-to-canonical-job persistence for PostgreSQL."""

    def __init__(self, db: Session):
        self.db = db

    def _update_discovery_events(
        self, raw_record_id: UUID, job_id: UUID | None, resolution_status: str
    ) -> bool:
        """Attach source observations to the canonicalization outcome and refresh last-seen."""
        events = self.db.scalars(
            select(JobDiscoveryEvent)
            .where(JobDiscoveryEvent.raw_source_record_id == raw_record_id)
            .with_for_update()
        ).all()
        if not events:
            return False

        latest_observed = max(event.observed_at for event in events)
        if latest_observed.tzinfo is None:
            latest_observed = latest_observed.replace(tzinfo=timezone.utc)
        for event in events:
            event.canonical_job_id = job_id
            rejected = job_id is None and resolution_status == "REJECTED"
            if job_id is not None:
                event.processing_status, event.processing_error = "CANONICALIZED", None
            elif rejected:
                # Permanent validation rejection: keep the traceable reason on replay.
                event.processing_status = "FAILED"
                event.processing_error = event.processing_error or "REJECTED"
            else:
                event.processing_status = "REQUIRES_REVIEW"
                event.processing_error = resolution_status[:64]

        if job_id is not None:
            job = self.db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is not None:
                last_observed = job.last_observed_at
                if last_observed.tzinfo is None:
                    last_observed = last_observed.replace(tzinfo=timezone.utc)
                if latest_observed > last_observed:
                    job.last_observed_at = latest_observed
                    job.updated_at = datetime.now(timezone.utc)
        return True

    def _reject_unusable_record(self, raw: RawSourceRecord, reason: str) -> dict:
        """Persist a permanent validation rejection; never fabricate a job or retry it."""
        now = datetime.now(timezone.utc)
        self.db.add(JobResolution(
            raw_source_record_id=raw.id, job_id=None, resolution_type="RESOLVE",
            resolution_method=f"VALIDATION_{reason}", confidence=0.0,
            resolver_version="exact-identity-v1", status="REJECTED", resolved_at=now,
        ))
        raw.status = "REJECTED"
        events = self.db.scalars(
            select(JobDiscoveryEvent)
            .where(JobDiscoveryEvent.raw_source_record_id == raw.id)
            .with_for_update()
        ).all()
        for event in events:
            event.canonical_job_id = None
            event.processing_status = "FAILED"
            event.processing_error = reason
        self.db.commit()
        return {"raw_source_record_id": str(raw.id), "job_id": None,
                "status": "REJECTED", "reason": reason, "replayed": False}

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
            existing_job_id = existing.job_id
            existing_status = existing.status
            # Do not roll back the session on an idempotent replay: callers may
            # have just recorded a new discovery observation/outbox event for this raw row.
            self._update_discovery_events(raw_record_id, existing_job_id, existing_status)
            self.db.commit()
            return {
                "raw_source_record_id": str(raw_record_id),
                "job_id": str(existing_job_id) if existing_job_id else None,
                "status": existing_status,
                "replayed": True,
            }

        source = self.db.get(Source, raw.source_id)
        if source is None:
            raise ValueError("Source for raw record not found")
        normalized = normalize_raw_record(raw.raw_content)
        if normalized["application_route_type"] is None:
            # Source/connector configuration may explicitly classify the route.
            # Connector configuration is the more specific default; raw per-job
            # metadata, when recognized, always takes precedence. Never infer from URL.
            run = self.db.get(AcquisitionRun, raw.acquisition_run_id)
            connector = self.db.get(SourceConnector, run.source_connector_id) if run else None
            configuration_version = (
                self.db.get(SourceConnectorConfigurationVersion, run.configuration_version_id)
                if run is not None and run.configuration_version_id is not None
                else None
            )
            # Interpret source defaults using the exact configuration that acquired
            # this record. A later publish/rollback must not rewrite the meaning of
            # already-acquired raw evidence while its outbox event is still pending.
            connector_config = (
                configuration_version.configuration
                if configuration_version is not None
                else connector.configuration if connector else None
            )
            source_config = source.configuration
            for config in (connector_config, source_config):
                if not isinstance(config, dict):
                    continue
                configured_route = normalize_application_route_type(
                    config.get("application_route_type")
                    or config.get("application_route_classification")
                )
                if configured_route is not None:
                    normalized["application_route_type"] = configured_route
                    break
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
        normalized_source_url = normalize_url(raw.source_url)
        if normalized_source_url:
            identity_lock_keys.append(f"source-url:{normalized_source_url}")
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

        # A stable source-scoped external ID can identify an existing posting even
        # when a later observation omits its title. Without that strong identity
        # signal, a title-less record is not safe to canonicalize or create.
        if not normalized["canonical_title"] and not candidates_by_id:
            return self._reject_unusable_record(raw, "MISSING_TITLE")

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

        # Posting/source URLs are identity evidence, but never application destinations.
        # Reuse a canonical job only when its title and company remain consistent.
        if normalized_source_url:
            rows = self.db.scalars(
                select(Job)
                .join(JobProvenance, JobProvenance.job_id == Job.id)
                .where(
                    or_(
                        JobProvenance.normalized_source_url == normalized_source_url,
                        # Compatibility for directly-created legacy provenance rows.
                        JobProvenance.source_url == raw.source_url,
                    ),
                    Job.identity_status == "CANONICAL",
                )
            ).all()
            incoming_title = " ".join(str(normalized["canonical_title"] or "").casefold().split())
            incoming_company = " ".join(str(normalized["company_name"] or "").casefold().split())
            for job in rows:
                prior_method = candidates_by_id.get(job.id, (None, None))[1]
                candidates_by_id.setdefault(job.id, (job, "EXACT_SOURCE_URL"))
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

        # Manual submissions may match an approved shared job, or a private job
        # already owned by this same candidate. Never merge two candidates' private-only
        # submissions into one canonical record.
        if source.source_type == "MANUAL_SUBMISSION":
            owner_value = raw.raw_content.get("submitted_by_user_id") if isinstance(raw.raw_content, dict) else None
            try:
                manual_owner_id = UUID(str(owner_value)) if owner_value else None
            except (TypeError, ValueError):
                manual_owner_id = None
            if manual_owner_id is None:
                candidates_by_id.clear()
                url_collision_job_ids.clear()
            elif candidates_by_id:
                visibility_by_job = {}
                provenance_rows = self.db.execute(
                    select(JobProvenance.job_id, JobProvenance.submitted_by_user_id, Source)
                    .join(Source, Source.id == JobProvenance.source_id)
                    .where(JobProvenance.job_id.in_(list(candidates_by_id)))
                ).all()
                for candidate_job_id, provenance_owner_id, candidate_source in provenance_rows:
                    state = visibility_by_job.setdefault(
                        candidate_job_id, {"public": False, "manual_owners": set()}
                    )
                    if (
                        candidate_source.source_type != "MANUAL_SUBMISSION"
                        and candidate_source.status == "ACTIVE"
                        and candidate_source.permission_review_status == "APPROVED"
                        and bool((candidate_source.permission_review_reference or "").strip())
                        and candidate_source.permission_reviewed_at is not None
                    ):
                        state["public"] = True
                    if candidate_source.source_type == "MANUAL_SUBMISSION" and provenance_owner_id is not None:
                        state["manual_owners"].add(provenance_owner_id)
                for candidate_job_id in list(candidates_by_id):
                    state = visibility_by_job.get(candidate_job_id, {"public": False, "manual_owners": set()})
                    if not state["public"] and state["manual_owners"] != {manual_owner_id}:
                        candidates_by_id.pop(candidate_job_id, None)
                        url_collision_job_ids.discard(candidate_job_id)

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
            self._update_discovery_events(raw_record_id, None, "REQUIRES_REVIEW")
            self.db.commit()
            return {
                "raw_source_record_id": str(raw_record_id),
                "job_id": None,
                "status": "REQUIRES_REVIEW",
                "candidate_job_ids": list(decision.candidate_job_ids),
                "replayed": False,
            }

        manual_owned_private_job = False
        if decision.status.value == "NEW":
            job = Job(
                identity_status="CANONICAL", lifecycle_status="DISCOVERED",
                freshness_status="UNKNOWN", canonical_title=normalized["canonical_title"],
                company_name=normalized["company_name"], employment_type=normalized["employment_type"],
                salary_min=normalized["salary_min"], salary_max=normalized["salary_max"],
                salary_currency=normalized["salary_currency"], salary_period=normalized["salary_period"],
                salary_type=normalized["salary_type"],
                original_salary_text=normalized["original_salary_text"],
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
            manual_submitter_id = None
            if source.source_type == "MANUAL_SUBMISSION":
                submitted_by_value = raw.raw_content.get("submitted_by_user_id")
                if submitted_by_value:
                    try:
                        manual_submitter_id = UUID(str(submitted_by_value))
                    except (TypeError, ValueError):
                        manual_submitter_id = None
            manual_owned_private_job = False
            if source.source_type == "MANUAL_SUBMISSION" and manual_submitter_id is not None:
                has_non_manual_provenance = self.db.scalar(
                    select(JobProvenance.id)
                    .join(Source, Source.id == JobProvenance.source_id)
                    .where(
                        JobProvenance.job_id == job.id,
                        Source.source_type != "MANUAL_SUBMISSION",
                    )
                    .limit(1)
                )
                has_owned_manual_provenance = self.db.scalar(
                    select(JobProvenance.id)
                    .where(
                        JobProvenance.job_id == job.id,
                        JobProvenance.submitted_by_user_id == manual_submitter_id,
                    )
                    .limit(1)
                )
                manual_owned_private_job = (
                    has_non_manual_provenance is None
                    and has_owned_manual_provenance is not None
                )
            # Only an actual non-manual source observation, or a repeat submission by
            # the same owner of a private manual job, may advance last-observed time.
            if source.source_type != "MANUAL_SUBMISSION" or manual_owned_private_job:
                job.last_observed_at = max(job.last_observed_at, raw.acquired_at)
            resolution_status = "MATCHED" if decision.status.value == "MATCHED" else "REQUIRES_REVIEW"
            resolution_method, confidence = decision.method, decision.confidence

        # A new manual job is private to its submitter. Repeated submissions by that
        # same owner may version their private JD, but user content cannot overwrite a
        # job that has any non-manual source provenance or another owner's private job.
        may_update_canonical_content = (
            source.source_type != "MANUAL_SUBMISSION"
            or decision.status.value == "NEW"
            or manual_owned_private_job
        )
        if may_update_canonical_content:
            metadata = dict(job.metadata_json or {})
            metadata["identity_fingerprint"] = fingerprint
            source_authoritative = resolution_method == "SOURCE_EXTERNAL_ID"

            # Same-source external IDs are the strongest source-level authority for
            # mutable canonical facts. Weaker matches may fill unknowns, not overwrite
            # facts already established by another source.
            mutable_fields = (
                ("canonical_title", normalized.get("canonical_title")),
                ("company_name", normalized.get("company_name")),
                ("employment_type", normalized.get("employment_type")),
                ("location_country", normalized.get("location_country")),
                ("location_region", normalized.get("location_region")),
                ("location_city", normalized.get("location_city")),
                ("work_mode", normalized.get("work_mode")),
                ("remote_eligibility", normalized.get("remote_eligibility")),
                ("original_location_text", normalized.get("original_location_text")),
            )
            for field, value in mutable_fields:
                if (
                    value is not None
                    and (not isinstance(value, str) or value.strip())
                    and (source_authoritative or getattr(job, field) is None)
                ):
                    setattr(job, field, value)

            incoming_application_url = normalized.get("application_url")
            if incoming_application_url is not None and (
                source_authoritative or job.application_url is None
            ):
                if incoming_application_url != job.application_url:
                    job.application_url = incoming_application_url
                    # Verification applies to the old URL only. Do not carry ACTIVE/FRESH
                    # forward when a trusted source changes the canonical route.
                    job.last_verified_at = None
                    job.lifecycle_status = "DISCOVERED"
                    job.freshness_status = "UNKNOWN"

            existing_seniority = metadata.get("seniority")
            if normalized.get("seniority") is not None and (
                source_authoritative
                or not isinstance(existing_seniority, str)
                or not existing_seniority.strip()
            ):
                metadata["seniority"] = normalized["seniority"]
            if raw.raw_content.get("external_job_id"):
                metadata["external_job_id"] = raw.raw_content["external_job_id"]
            if raw.raw_content.get("posting_date"):
                metadata["posting_date"] = raw.raw_content["posting_date"]
            if raw.raw_content.get("discovery_method"):
                metadata["discovery_method"] = raw.raw_content["discovery_method"]
            if raw.raw_content.get("submitted_at"):
                metadata["submitted_at"] = raw.raw_content["submitted_at"]
            # Preserve only explicit, typed eligibility facts. These remain source facts,
            # not inferred from location text or job-description wording.
            for key in ("application_fee_amount", "application_fee_currency", "relocation_required"):
                if normalized.get(key) is not None:
                    metadata[key] = normalized[key]
            # Route classification describes the canonical job's actual application URL.
            # A second source may describe the same vacancy through a different route;
            # its source-level default must not overwrite the classification for this URL.
            if (
                normalized.get("application_route_type") is not None
                and normalized.get("application_url")
                and normalized["application_url"] == job.application_url
            ):
                metadata["application_route_type"] = normalized["application_route_type"]
            if raw.raw_content.get("detected_email_source"):
                metadata["detected_email_source"] = raw.raw_content["detected_email_source"]
            if raw.raw_content.get("email_id"):
                metadata["email_id"] = raw.raw_content["email_id"]
            job.metadata_json = metadata
            # Canonical compensation is updated only when the observation supplies
            # explicit currency and period. Missing salary data never clears known facts.
            if (
                normalized.get("salary_currency")
                and normalized.get("salary_period")
                and (
                    normalized.get("salary_min") is not None
                    or normalized.get("salary_max") is not None
                )
                and (
                    source_authoritative
                    or job.salary_min is None
                    or job.salary_max is None
                    or job.salary_currency is None
                    or job.salary_period is None
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

        description = normalized["description"]
        if description and may_update_canonical_content:
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
        submitted_by_user_id = None
        if source.source_type == "MANUAL_SUBMISSION":
            submitted_by_value = raw.raw_content.get("submitted_by_user_id")
            if submitted_by_value:
                try:
                    submitted_by_user_id = UUID(str(submitted_by_value))
                except (TypeError, ValueError):
                    # Malformed ownership metadata must never grant private access.
                    submitted_by_user_id = None

        self.db.add(JobProvenance(
            job_id=job.id, source_id=raw.source_id, raw_source_record_id=raw.id,
            submitted_by_user_id=submitted_by_user_id,
            source_url=raw.source_url, normalized_source_url=normalized_source_url,
            observation_type="ACQUIRED", asserted_by="SOURCE",
            observed_at=raw.acquired_at, confidence=confidence, status="OBSERVED",
            metadata_json={"identity_fingerprint": fingerprint,
                           "description_hash": normalized["description_hash"]},
        ))
        self._update_discovery_events(raw_record_id, job.id, resolution_status)
        self.db.commit()
        return {"raw_source_record_id": str(raw_record_id), "job_id": str(job.id),
                "status": resolution_status, "replayed": False}
