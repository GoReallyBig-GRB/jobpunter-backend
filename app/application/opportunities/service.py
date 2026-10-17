import uuid
from datetime import datetime, timezone
from uuid import UUID
import re

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.audit.service import AuditEventService
from app.domain.jobs.completeness import has_substantive_job_description
from app.application.ai.output_contracts import get_task_output_contract_version
from app.application.ai.system_policy import SYSTEM_POLICY_VERSION
from app.application.ai.task_registry import get_ai_task_definition
from app.application.opportunities.evaluation_policy import POLICY_VERSION
from app.application.opportunities.opportunity_feed_schemas import (
    CandidateOpportunityFeedItem,
    CandidateOpportunityFeedResponse,
    CandidateOpportunityPagination,
    OpportunityDescriptionResponse,
    OpportunityEvaluationResponse,
    OpportunityJobResponse,
    OpportunityProvenanceResponse,
)
from app.application.jobs.access import (
    candidate_can_access_job,
    candidate_job_visibility_predicate,
    public_source_visibility_predicate,
)
from app.application.jobs.job_verification import current_application_route_verification
from app.application.jobs.requirement_snapshot import (
    snapshot_job_requirements,
    snapshot_requirement_extraction_trace,
)
from app.application.application_packages.review import ApplicationPackageReviewService
from app.domain.opportunities.service import qualify_punt, transition_application
from app.infrastructure.db.models import (
    Application, ApplicationEvent, ApplicationPackage, ApplicationPackageItem, CandidateClaim,
    CandidateEvaluation, CandidateEvaluationOverride, CandidatePreferences, CandidateProfile, CareerPathway, Evidence, Job, JobDescriptionVersion,
    AuditEvent, JobDiscoveryEvent, JobProvenance, RawSourceRecord, Source, Punt, PuntEvaluation, PuntQualificationHistory,
    GovernanceConfigVersion, OutboxEvent,
)


class OpportunityService:
    """Candidate-scoped persistence for evaluations, Punts and applications."""

    def __init__(self, db: Session):
        self.db = db

    def _profile(self, user_id: UUID) -> CandidateProfile:
        profile = self.db.scalar(
            select(CandidateProfile).where(CandidateProfile.user_id == user_id)
        )
        if profile is None:
            raise DomainError("CANDIDATE_PROFILE_NOT_FOUND", "Candidate profile not found", 404)
        return profile

    def list_opportunity_feed(
        self,
        user_id: UUID,
        *,
        limit: int = 20,
        offset: int = 0,
        outcome: str | None = None,
        validity_status: str | None = None,
        freshness_status: str | None = None,
        lifecycle_status: str | None = None,
        query: str | None = None,
    ) -> CandidateOpportunityFeedResponse:
        """Return the latest candidate-specific evaluation for each job/target-role pair.

        A job can appear more than once when it has distinct target-role evaluations.
        Every item includes the current canonical job data and source provenance needed
        to render an explainable, candidate-specific opportunity card.
        """
        if limit < 1 or limit > 50 or offset < 0:
            raise DomainError("INVALID_PAGINATION", "limit must be 1–50 and offset must be non-negative", 422)
        profile = self._profile(user_id)
        latest_versions = (
            select(
                CandidateEvaluation.candidate_profile_id.label("candidate_profile_id"),
                CandidateEvaluation.job_id.label("job_id"),
                CandidateEvaluation.target_role_key.label("target_role_key"),
                func.max(CandidateEvaluation.evaluation_version).label("latest_version"),
            )
            .where(CandidateEvaluation.candidate_profile_id == profile.id)
            .group_by(
                CandidateEvaluation.candidate_profile_id,
                CandidateEvaluation.job_id,
                CandidateEvaluation.target_role_key,
            )
            .subquery()
        )
        base = (
            select(CandidateEvaluation, Job)
            .join(
                latest_versions,
                and_(
                    CandidateEvaluation.candidate_profile_id == latest_versions.c.candidate_profile_id,
                    CandidateEvaluation.job_id == latest_versions.c.job_id,
                    CandidateEvaluation.target_role_key == latest_versions.c.target_role_key,
                    CandidateEvaluation.evaluation_version == latest_versions.c.latest_version,
                ),
            )
            .join(Job, Job.id == CandidateEvaluation.job_id)
            .where(
                CandidateEvaluation.candidate_profile_id == profile.id,
                candidate_job_visibility_predicate(user_id),
            )
        )
        # Validate stale snapshots in bounded keyset batches rather than loading every
        # candidate evaluation into application memory.
        last_evaluation_id = None
        while True:
            scan = base.order_by(CandidateEvaluation.id).limit(200)
            if last_evaluation_id is not None:
                scan = scan.where(CandidateEvaluation.id > last_evaluation_id)
            batch = list(self.db.execute(scan).all())
            if not batch:
                break
            changed = False
            for evaluation, _job in batch:
                if evaluation.validity_status == "CURRENT" and not self._evaluation_inputs_are_current(
                    profile, evaluation, datetime.now(timezone.utc)
                ):
                    evaluation.validity_status = "STALE"
                    changed = True
            last_evaluation_id = batch[-1][0].id
            if changed:
                self.db.commit()

        filters = []
        normalized_outcome = outcome.strip().upper() if outcome else None
        normalized_validity = validity_status.strip().upper() if validity_status else None
        normalized_freshness = freshness_status.strip().upper() if freshness_status else None
        normalized_lifecycle = lifecycle_status.strip().upper() if lifecycle_status else None
        needle = query.strip() if query else None

        if normalized_outcome:
            filters.append(CandidateEvaluation.outcome == normalized_outcome)
        if normalized_validity:
            filters.append(CandidateEvaluation.validity_status == normalized_validity)
        if normalized_freshness:
            filters.append(Job.freshness_status == normalized_freshness)
        if normalized_lifecycle:
            filters.append(Job.lifecycle_status == normalized_lifecycle)
        if needle:
            pattern = f"%{needle}%"
            filters.append(or_(
                func.coalesce(Job.canonical_title, "").ilike(pattern),
                func.coalesce(Job.company_name, "").ilike(pattern),
                func.coalesce(Job.original_location_text, "").ilike(pattern),
                func.coalesce(Job.location_city, "").ilike(pattern),
                func.coalesce(Job.location_region, "").ilike(pattern),
                func.coalesce(Job.location_country, "").ilike(pattern),
            ))

        filtered_base = base.where(*filters)
        total = self.db.scalar(
            select(func.count()).select_from(filtered_base.subquery())
        ) or 0
        selected = list(self.db.execute(
            filtered_base
            .order_by(
                CandidateEvaluation.evaluated_at.desc(),
                Job.last_observed_at.desc(),
                Job.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).all())
        if not selected:
            return CandidateOpportunityFeedResponse(
                data=[],
                pagination=CandidateOpportunityPagination(limit=limit, offset=offset, total=total),
            )

        job_ids = list({job.id for _evaluation, job in selected})
        descriptions = list(self.db.scalars(
            select(JobDescriptionVersion)
            .where(JobDescriptionVersion.job_id.in_(job_ids))
            .order_by(JobDescriptionVersion.job_id, JobDescriptionVersion.version_number.desc())
        ).all())
        latest_descriptions: dict[UUID, JobDescriptionVersion] = {}
        for description in descriptions:
            latest_descriptions.setdefault(description.job_id, description)

        observation_stats: dict[tuple[UUID, UUID], tuple[datetime | None, datetime | None, int]] = {}
        event_rows = self.db.execute(
            select(
                JobDiscoveryEvent.canonical_job_id,
                JobDiscoveryEvent.source_id,
                func.min(JobDiscoveryEvent.observed_at),
                func.max(JobDiscoveryEvent.observed_at),
                func.count(JobDiscoveryEvent.id),
            )
            .where(JobDiscoveryEvent.canonical_job_id.in_(job_ids))
            .group_by(JobDiscoveryEvent.canonical_job_id, JobDiscoveryEvent.source_id)
        ).all()
        for job_id, source_id, first_seen, last_seen, count in event_rows:
            observation_stats[(job_id, source_id)] = (first_seen, last_seen, int(count))

        provenance_stats: dict[tuple[UUID, UUID], tuple[datetime | None, datetime | None, int]] = {}
        provenance_stat_rows = self.db.execute(
            select(
                JobProvenance.job_id,
                JobProvenance.source_id,
                func.min(JobProvenance.observed_at),
                func.max(JobProvenance.observed_at),
                func.count(JobProvenance.id),
            )
            .where(JobProvenance.job_id.in_(job_ids))
            .group_by(JobProvenance.job_id, JobProvenance.source_id)
        ).all()
        for job_id, source_id, first_seen, last_seen, count in provenance_stat_rows:
            provenance_stats[(job_id, source_id)] = (first_seen, last_seen, int(count))

        latest_provenance_rows = self.db.execute(
            select(
                JobProvenance,
                Source.name,
                Source.source_type,
                RawSourceRecord.external_record_id,
            )
            .join(Source, Source.id == JobProvenance.source_id)
            .outerjoin(RawSourceRecord, RawSourceRecord.id == JobProvenance.raw_source_record_id)
            .where(
                JobProvenance.job_id.in_(job_ids),
                or_(
                    public_source_visibility_predicate(),
                    and_(
                        Source.source_type == "MANUAL_SUBMISSION",
                        JobProvenance.submitted_by_user_id == user_id,
                    ),
                ),
            )
            .order_by(JobProvenance.observed_at.desc(), JobProvenance.id.desc())
        ).all()
        provenance_by_job: dict[UUID, list[OpportunityProvenanceResponse]] = {}
        seen_provenance: set[tuple[UUID, UUID]] = set()
        for provenance, source_name, source_type, external_job_id in latest_provenance_rows:
            key = (provenance.job_id, provenance.source_id)
            if key in seen_provenance:
                continue
            seen_provenance.add(key)
            stats = observation_stats.get(key) or provenance_stats.get(key) or (
                provenance.observed_at, provenance.observed_at, 1
            )
            provenance_by_job.setdefault(provenance.job_id, []).append(
                OpportunityProvenanceResponse(
                    source_id=provenance.source_id,
                    source_name=source_name,
                    source_type=source_type,
                    source_url=provenance.source_url,
                    external_job_id=external_job_id,
                    first_seen_at=stats[0],
                    last_seen_at=stats[1],
                    observation_count=stats[2],
                    observation_type=provenance.observation_type,
                    status=provenance.status,
                )
            )

        punts = self.db.scalars(
            select(Punt).where(
                Punt.candidate_profile_id == profile.id,
                Punt.job_id.in_(job_ids),
            )
        ).all()
        punt_status_by_job = {punt.job_id: punt.status for punt in punts}
        items = []
        now = datetime.now(timezone.utc)
        for evaluation, job in selected:
            description = latest_descriptions.get(job.id)
            stored_metadata = job.metadata_json if isinstance(job.metadata_json, dict) else {}
            # Expose only candidate-safe job facts. Source-specific IDs and manual-email
            # parser metadata stay in private raw/provenance records.
            metadata = {
                key: stored_metadata[key]
                for key in (
                    "application_route_classification", "application_route_type",
                    "posting_date", "application_fee_amount",
                    "application_fee_currency", "relocation_required",
                )
                if key in stored_metadata
            }
            description_payload = None
            if description is not None:
                description_payload = OpportunityDescriptionResponse(
                    version_number=description.version_number,
                    content=description.content,
                    content_hash=description.content_hash,
                    completeness_status=description.completeness_status,
                    retrieved_at=description.retrieved_at,
                    requirement_extraction_status=description.requirement_extraction_status,
                )
            job_payload = OpportunityJobResponse(
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
                application_route_type=(
                    metadata.get("application_route_classification")
                    or metadata.get("application_route_type")
                ),
                application_route_verification=current_application_route_verification(
                    self.db, job, now=now,
                ),
                lifecycle_status=str(job.lifecycle_status or "UNKNOWN"),
                freshness_status=str(job.freshness_status or "UNKNOWN"),
                first_discovered_at=job.first_discovered_at,
                last_observed_at=job.last_observed_at,
                last_verified_at=job.last_verified_at,
                metadata=metadata,
                description=description_payload,
                provenance=provenance_by_job.get(job.id, []),
                punt_status=punt_status_by_job.get(job.id),
            )
            evaluation_payload = OpportunityEvaluationResponse(
                id=evaluation.id,
                job_description_version_id=evaluation.job_description_version_id,
                target_role_key=evaluation.target_role_key,
                evaluation_version=evaluation.evaluation_version,
                validity_status=evaluation.validity_status,
                outcome=evaluation.outcome,
                score=float(evaluation.score) if evaluation.score is not None else None,
                hard_blockers=evaluation.hard_blockers or [],
                evidence_summary=evaluation.evidence_summary or {},
                explanation=evaluation.explanation or {},
                policy_version=evaluation.policy_version,
                evaluated_at=evaluation.evaluated_at,
            )
            items.append(CandidateOpportunityFeedItem(job=job_payload, evaluation=evaluation_payload))
        return CandidateOpportunityFeedResponse(
            data=items,
            pagination=CandidateOpportunityPagination(limit=limit, offset=offset, total=total),
        )

    def get_evaluation(self, user_id: UUID, evaluation_id: UUID) -> CandidateEvaluation:
        """Return one candidate-owned evaluation, revalidating its snapshot before exposure."""
        profile = self._profile(user_id)
        evaluation = self.db.scalar(
            select(CandidateEvaluation).where(
                CandidateEvaluation.id == evaluation_id,
                CandidateEvaluation.candidate_profile_id == profile.id,
            )
        )
        if evaluation is None or not candidate_can_access_job(
            self.db, user_id, evaluation.job_id
        ):
            raise DomainError("EVALUATION_NOT_FOUND", "Evaluation not found", 404)

        if evaluation.validity_status == "CURRENT" and not self._evaluation_inputs_are_current(
            profile, evaluation, datetime.now(timezone.utc)
        ):
            evaluation.validity_status = "STALE"
            self.db.commit()
        return evaluation

    def list_evaluations(self, user_id: UUID) -> list[CandidateEvaluation]:
        profile = self._profile(user_id)
        rows = list(self.db.scalars(
            select(CandidateEvaluation)
            .where(CandidateEvaluation.candidate_profile_id == profile.id)
            .order_by(CandidateEvaluation.evaluated_at.desc())
        ).all())

        # Do not expose an evaluation as CURRENT after its source inputs have changed.
        # Punt creation performs the same check as a final guard, but listing must not
        # leave stale recommendations appearing current until the candidate acts on them.
        now = datetime.now(timezone.utc)
        changed = False
        for row in rows:
            if row.validity_status == "CURRENT" and not self._evaluation_inputs_are_current(
                profile, row, now
            ):
                row.validity_status = "STALE"
                changed = True
        if changed:
            self.db.commit()
        return rows

    def _evaluation_inputs_are_current(
        self, profile: CandidateProfile, evaluation: CandidateEvaluation, now: datetime
    ) -> bool:
        """Compare a persisted evaluation's provenance snapshot with current inputs."""
        decision_rule_config = self.db.scalar(
            select(GovernanceConfigVersion).where(
                GovernanceConfigVersion.config_key == "decision.rules.candidate_evaluation",
                GovernanceConfigVersion.status == "PUBLISHED",
            )
        )
        current_rule_config_id = str(decision_rule_config.id) if decision_rule_config else None
        current_rule_config_version = decision_rule_config.version if decision_rule_config else None
        current_rule_config_fingerprint = (
            decision_rule_config.payload_fingerprint if decision_rule_config else "builtin"
        )
        current_policy_version = (
            f"{POLICY_VERSION}+rules-v{decision_rule_config.version}"
            if decision_rule_config else POLICY_VERSION
        )
        snapshot = evaluation.input_snapshot
        required = {
            "candidate_profile_revision", "candidate_profile_data", "approved_evidence", "approved_claims", "excluded_claims",
            "preferences_revision", "preferences", "job_id", "job_revision",
            "job_description_version_id", "job_description_version_number",
            "job_description_content_hash", "job_description_completeness_status",
            "job_description_requirement_extraction_status", "job_description_requirement_extraction_version",
            "job_requirements", "job_requirement_extraction_trace", "job_snapshot",
            "decision_rule_config_version_id", "decision_rule_config_version",
            "decision_rule_config_fingerprint",
            "evaluation_target_role_key", "evaluation_policy_version", "ai_trace",
        }
        if not isinstance(snapshot, dict) or not required.issubset(snapshot):
            return False
        if (
            snapshot["candidate_profile_revision"] != profile.revision
            or snapshot["candidate_profile_revision"] != evaluation.candidate_profile_revision
            or snapshot["preferences_revision"] != evaluation.preferences_revision
            or snapshot["evaluation_target_role_key"] != evaluation.target_role_key
            or snapshot["evaluation_policy_version"] != evaluation.policy_version
            or snapshot["evaluation_policy_version"] != current_policy_version
            or snapshot["decision_rule_config_version_id"] != current_rule_config_id
            or snapshot["decision_rule_config_version"] != current_rule_config_version
            or snapshot["decision_rule_config_fingerprint"] != current_rule_config_fingerprint
            or snapshot["job_description_version_id"] != (
                str(evaluation.job_description_version_id)
                if evaluation.job_description_version_id else None
            )
        ):
            return False
        if snapshot.get("candidate_profile_data") != (profile.profile_data or {}):
            return False

        current_task_config = self.db.scalar(select(GovernanceConfigVersion).where(
            GovernanceConfigVersion.config_key == "ai.task.evidence_matching",
            GovernanceConfigVersion.status == "PUBLISHED",
        ))
        current_task_config_id = str(current_task_config.id) if current_task_config else None
        try:
            current_task_policy_version = get_ai_task_definition("evidence_matching").policy_version
            current_contract_version = get_task_output_contract_version("evidence_matching")
        except (KeyError, ValueError):
            return False

        for trace in snapshot["ai_trace"]:
            if not isinstance(trace, dict) or trace.get("task") != "evidence_matching":
                continue
            if (
                trace.get("output_contract_version") != current_contract_version
                or trace.get("task_policy_version") != current_task_policy_version
                or trace.get("system_policy_version") != SYSTEM_POLICY_VERSION
                or trace.get("config_version_id") != current_task_config_id
                or not trace.get("config_fingerprint")
                or trace.get("config_fingerprint") == "unversioned"
            ):
                return False

        extraction_trace = snapshot["job_requirement_extraction_trace"]
        if extraction_trace is not None:
            try:
                current_extraction_contract = get_task_output_contract_version("job_requirement_extraction")
            except (KeyError, ValueError):
                return False
            if extraction_trace.get("output_contract_version") != current_extraction_contract:
                return False

        preferences_row = self.db.scalar(
            select(CandidatePreferences)
            .where(CandidatePreferences.candidate_profile_id == profile.id)
            .with_for_update()
        )
        current_preferences = preferences_row.preferences if preferences_row else {}
        current_preferences_revision = preferences_row.revision if preferences_row else 0
        if (
            current_preferences_revision != snapshot["preferences_revision"]
            or current_preferences != snapshot["preferences"]
        ):
            return False

        # Evaluations tied to an approved pathway become stale if that pathway is
        # edited, retired, rejected, or newly matches this target role.
        if "career_pathway_snapshot" in snapshot:
            normalize_role = lambda value: "-".join(
                part for part in re.split(r"[^a-z0-9]+", value.strip().casefold()) if part
            )
            current_pathways = self.db.scalars(select(CareerPathway).where(
                CareerPathway.candidate_profile_id == profile.id,
                CareerPathway.approval_status == "APPROVED",
            ).order_by(CareerPathway.id)).all()
            current_pathway_snapshot = [
                {
                    "id": str(pathway.id),
                    "revision": pathway.revision,
                    "title": pathway.title,
                    "role_family": pathway.role_family,
                    "target_roles": pathway.target_roles,
                    "target_seniority": pathway.target_seniority,
                    "domains": pathway.domains,
                }
                for pathway in current_pathways
                if any(
                    normalize_role(role) == normalize_role(evaluation.target_role_key)
                    for role in pathway.target_roles
                    if isinstance(role, str) and role.strip()
                )
            ]
            if current_pathway_snapshot != snapshot["career_pathway_snapshot"]:
                return False

        evidence_rows = list(self.db.scalars(
            select(Evidence)
            .where(Evidence.candidate_profile_id == profile.id)
            .order_by(Evidence.updated_at.desc(), Evidence.id)
            .with_for_update()
        ).all())
        current_preferences_for_exclusions = preferences_row.preferences if preferences_row else {}
        raw_exclusions = current_preferences_for_exclusions.get(
            "evidenceExclusions", current_preferences_for_exclusions.get("evidence_exclusions", [])
        )
        excluded_evidence_ids = {str(item) for item in raw_exclusions if isinstance(item, str)}
        current_evidence = [
            {"id": str(row.id), "type": row.evidence_type, "revision": row.revision, "content": row.content}
            for row in evidence_rows
            if row.status == "APPROVED"
            and (row.effective_from is None or row.effective_from <= now)
            and (row.effective_to is None or row.effective_to > now)
            and (row.expires_at is None or row.expires_at > now)
            and str(row.id) not in excluded_evidence_ids
        ]
        snapshot_evidence = sorted(
            snapshot["approved_evidence"], key=lambda item: item.get("id", "")
        )
        if sorted(current_evidence, key=lambda item: item["id"]) != snapshot_evidence:
            return False

        claim_rows = list(self.db.scalars(
            select(CandidateClaim)
            .where(CandidateClaim.candidate_profile_id == profile.id)
            .order_by(CandidateClaim.created_at, CandidateClaim.id)
            .with_for_update()
        ).all())
        current_claims = [
            {"id": str(row.id), "type": row.claim_type, "revision": row.revision, "content": row.claim_content}
            for row in claim_rows
            if row.status == "APPROVED"
            and (row.effective_from is None or row.effective_from <= now)
            and (row.effective_to is None or row.effective_to > now)
        ]
        snapshot_claims = [
            {key: item[key] for key in ("id", "type", "revision", "content")}
            for item in snapshot["approved_claims"] + snapshot["excluded_claims"]
        ]
        if sorted(current_claims, key=lambda item: item["id"]) != sorted(
            snapshot_claims, key=lambda item: item["id"]
        ):
            return False

        job = self.db.scalar(
            select(Job).where(Job.id == evaluation.job_id).with_for_update()
        )
        if (
            job is None
            or str(job.id) != snapshot["job_id"]
            or job.revision != snapshot["job_revision"]
        ):
            return False
        current_job_snapshot = {
            "id": str(job.id),
            "canonical_title": job.canonical_title,
            "company_name": job.company_name,
            "employment_type": job.employment_type,
            "lifecycle_status": job.lifecycle_status,
            "salary_min": float(job.salary_min) if job.salary_min is not None else None,
            "salary_max": float(job.salary_max) if job.salary_max is not None else None,
            "salary_currency": job.salary_currency,
            "salary_period": job.salary_period,
            "location_country": job.location_country,
            "work_mode": job.work_mode,
            "location_restrictions": job.location_restrictions,
            "metadata": job.metadata_json or {},
            "freshness_status": job.freshness_status,
            "application_url": job.application_url,
            "application_route_verification": current_application_route_verification(
                self.db, job, now=now,
            ),
        }
        if current_job_snapshot != snapshot["job_snapshot"]:
            return False

        latest_description = self.db.scalar(
            select(JobDescriptionVersion)
            .where(JobDescriptionVersion.job_id == evaluation.job_id)
            .order_by(JobDescriptionVersion.version_number.desc())
            .limit(1)
            .with_for_update()
        )
        current_description_id = str(latest_description.id) if latest_description else None
        current_description_number = latest_description.version_number if latest_description else None
        current_description_hash = latest_description.content_hash if latest_description else None
        current_description_completeness = (
            latest_description.completeness_status if latest_description else "MISSING"
        )
        current_extraction_status = (
            latest_description.requirement_extraction_status if latest_description else "MISSING"
        )
        current_extraction_version = (
            latest_description.requirement_extraction_version if latest_description else None
        )
        if (
            current_description_id != snapshot["job_description_version_id"]
            or current_description_number != snapshot["job_description_version_number"]
            or current_description_hash != snapshot["job_description_content_hash"]
            or current_description_completeness != snapshot["job_description_completeness_status"]
            or current_extraction_status != snapshot["job_description_requirement_extraction_status"]
            or current_extraction_version != snapshot["job_description_requirement_extraction_version"]
        ):
            return False
        current_requirements = snapshot_job_requirements(
            self.db, latest_description.id if latest_description else None,
        )
        if current_requirements != snapshot["job_requirements"]:
            return False
        current_extraction_trace = snapshot_requirement_extraction_trace(
            self.db, latest_description.id if latest_description else None,
        )
        return current_extraction_trace == snapshot["job_requirement_extraction_trace"]

    def create_punt(self, user_id: UUID, evaluation_id: UUID) -> Punt:
        profile = self._profile(user_id)
        # Serialize Punt qualification against concurrent profile revisions. The
        # evaluator and profile PATCH use the same profile-row lock as their gate.
        profile = self.db.scalar(
            select(CandidateProfile)
            .where(CandidateProfile.id == profile.id)
            .with_for_update()
        )
        evaluation = self.db.scalar(
            select(CandidateEvaluation)
            .where(
                CandidateEvaluation.id == evaluation_id,
                CandidateEvaluation.candidate_profile_id == profile.id,
            )
            .with_for_update()
        )
        if evaluation is None:
            raise DomainError("EVALUATION_NOT_FOUND", "Evaluation not found", 404)
        now = datetime.now(timezone.utc)
        if evaluation.validity_status == "CURRENT" and not self._evaluation_inputs_are_current(
            profile, evaluation, now
        ):
            evaluation.validity_status = "STALE"
            self.db.commit()
            raise DomainError(
                "EVALUATION_STALE",
                "The candidate, preferences, or job inputs changed after this evaluation. Re-evaluate before creating a Punt.",
                409,
            )
        job = self.db.scalar(
            select(Job).where(Job.id == evaluation.job_id).with_for_update()
        )
        if job is None or not candidate_can_access_job(self.db, user_id, job.id):
            raise DomainError("JOB_NOT_FOUND", "Job not found", 404)
        lifecycle = str(job.lifecycle_status or "").upper()
        freshness = str(job.freshness_status or "UNKNOWN").upper()
        if lifecycle not in {"ACTIVE", "OPEN"}:
            raise DomainError(
                "JOB_NOT_OPEN",
                "Only jobs currently recorded as ACTIVE or OPEN can qualify for a Punt.",
                409,
                {"lifecycle_status": lifecycle or "UNKNOWN"},
            )
        if freshness not in {"FRESH", "CURRENT"}:
            raise DomainError(
                "JOB_FRESHNESS_UNVERIFIED",
                "The job is stale or its freshness is unknown. Reverify it before creating a Punt.",
                409,
                {"freshness_status": freshness},
            )
        route_verification = current_application_route_verification(self.db, job, now=now)
        if route_verification["status"] != "ACTIVE":
            raise DomainError(
                "JOB_APPLICATION_ROUTE_UNVERIFIED",
                "A current successful verification of the job's application route is required before creating a Punt.",
                409,
                {"application_route_status": route_verification["status"]},
            )
        latest_description = self.db.scalar(
            select(JobDescriptionVersion)
            .where(JobDescriptionVersion.job_id == job.id)
            .order_by(JobDescriptionVersion.version_number.desc())
            .limit(1)
            .with_for_update()
        )
        if (
            latest_description is None
            or latest_description.completeness_status not in {"COMPLETE", "FULL"}
            or not latest_description.content
        ):
            raise DomainError(
                "JOB_DESCRIPTION_NOT_EVALUABLE",
                "A current, substantively complete job description is required before creating a Punt.",
                409,
                {"completeness_status": latest_description.completeness_status if latest_description else "MISSING"},
            )
        from app.domain.jobs.completeness import has_substantive_job_description
        if not has_substantive_job_description(latest_description.content):
            raise DomainError(
                "JOB_DESCRIPTION_NOT_EVALUABLE",
                "The latest job description does not contain substantive responsibilities and requirements.",
                409,
                {"completeness_status": latest_description.completeness_status},
            )
        try:
            qualify_punt(evaluation)
        except ValueError as exc:
            raise DomainError("EVALUATION_NOT_QUALIFIED", str(exc), 409) from exc

        inserted_id = self.db.scalar(
            pg_insert(Punt)
            .values(
                candidate_profile_id=profile.id,
                job_id=evaluation.job_id,
                status="ACTIVE",
                qualified_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_punt_candidate_job")
            .returning(Punt.id)
        )
        created = inserted_id is not None
        punt = self.db.scalar(
            select(Punt).where(
                Punt.candidate_profile_id == profile.id,
                Punt.job_id == evaluation.job_id,
            ).with_for_update()
        )
        if punt is None:
            raise RuntimeError("Punt insert conflicted but the canonical Punt could not be reloaded")

        # Multiple distinct evaluations for the same candidate/job can qualify concurrently.
        # Make the association idempotent at the database boundary as well as the Punt insert.
        self.db.execute(
            pg_insert(PuntEvaluation)
            .values(punt_id=punt.id, evaluation_id=evaluation.id)
            .on_conflict_do_nothing()
        )
        # Keep an immutable, queryable qualification record separate from the mutable
        # Punt aggregate and the evaluation-link table. The unique key makes retries
        # and concurrent duplicate requests idempotent.
        self.db.execute(
            pg_insert(PuntQualificationHistory)
            .values(
                punt_id=punt.id,
                candidate_profile_id=profile.id,
                job_id=evaluation.job_id,
                evaluation_id=evaluation.id,
                actor_user_id=user_id,
                evaluation_version=evaluation.evaluation_version,
                outcome=evaluation.outcome,
                score=evaluation.score,
                policy_version=evaluation.policy_version,
                hard_blockers=evaluation.hard_blockers or [],
                qualified_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_punt_qualification_history_punt_evaluation")
        )
        if created:
            self.db.add(OutboxEvent(
                event_type="PUNT_QUALIFIED",
                aggregate_type="Punt",
                aggregate_id=punt.id,
                payload={"punt_id": str(punt.id)},
                status="PENDING",
                attempt_count=0,
                available_at=now,
            ))
        self.db.commit()
        self.db.refresh(punt)
        return punt

    def list_punts(self, user_id: UUID) -> list[Punt]:
        profile = self._profile(user_id)
        return list(self.db.scalars(
            select(Punt).where(Punt.candidate_profile_id == profile.id)
            .order_by(Punt.qualified_at.desc())
        ).all())

    def create_application(self, user_id: UUID, punt_id: UUID) -> Application:
        profile = self._profile(user_id)
        punt = self.db.scalar(
            select(Punt).where(
                Punt.id == punt_id, Punt.candidate_profile_id == profile.id
            ).with_for_update()
        )
        if punt is None:
            raise DomainError("PUNT_NOT_FOUND", "Punt not found", 404)
        if not candidate_can_access_job(self.db, user_id, punt.job_id):
            raise DomainError("JOB_NOT_FOUND", "Job not found", 404)
        now = datetime.now(timezone.utc)
        proposed_id = uuid.uuid4()
        inserted_id = self.db.scalar(
            pg_insert(Application)
            .values(
                id=proposed_id,
                candidate_profile_id=profile.id,
                job_id=punt.job_id,
                punt_id=punt.id,
                status="PLANNED",
                revision=1,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_application_candidate_job")
            .returning(Application.id)
        )
        created = inserted_id is not None
        application = self.db.scalar(
            select(Application).where(
                Application.candidate_profile_id == profile.id,
                Application.job_id == punt.job_id,
            ).with_for_update()
        )
        if application is None:
            raise RuntimeError("Application insert conflicted but the canonical application could not be reloaded")
        if created:
            self.db.add(ApplicationEvent(
                application_id=application.id,
                event_type="APPLICATION_CREATED",
                from_status=None,
                to_status="PLANNED",
                actor_type="USER",
                actor_id=profile.user_id,
                occurred_at=now,
                reason="Application created from a qualified Punt",
                notes=None,
                metadata_json={"source": "QUALIFIED_PUNT_WORKFLOW", "punt_id": str(punt.id)},
            ))
        self.db.commit()
        self.db.refresh(application)
        return application

    def list_applications(self, user_id: UUID) -> list[Application]:
        profile = self._profile(user_id)
        return list(self.db.scalars(
            select(Application).where(Application.candidate_profile_id == profile.id)
            .order_by(Application.updated_at.desc())
        ).all())

    def transition_application(
        self,
        user_id: UUID,
        application_id: UUID,
        target_status: str,
        expected_revision: int,
        reason: str,
        notes: str | None = None,
    ) -> Application:
        profile = self._profile(user_id)
        application = self.db.scalar(
            select(Application).where(
                Application.id == application_id,
                Application.candidate_profile_id == profile.id,
            ).with_for_update()
        )
        if application is None:
            raise DomainError("APPLICATION_NOT_FOUND", "Application not found", 404)
        if application.revision != expected_revision:
            raise DomainError(
                "REVISION_CONFLICT", "Resource revision is stale", 409,
                {"current_revision": application.revision},
            )
        target_status = target_status.strip().upper()
        if target_status == "SUBMITTED" and application.status != "APPROVED":
            raise DomainError(
                "INVALID_STATE_TRANSITION",
                f"Invalid application transition: {application.status} -> {target_status}",
                409,
            )
        if target_status == "APPROVED":
            job = self.db.scalar(
                select(Job).where(Job.id == application.job_id).with_for_update()
            )
            if job is None or not candidate_can_access_job(self.db, user_id, job.id):
                raise DomainError("JOB_NOT_FOUND", "Application job not found", 404)
            if job.lifecycle_status not in {"ACTIVE", "OPEN"} or job.freshness_status not in {"FRESH", "CURRENT"}:
                raise DomainError(
                    "JOB_NOT_CURRENT",
                    "The job must be active and freshly verified before approving an application.",
                    409,
                    {"lifecycle_status": job.lifecycle_status, "freshness_status": job.freshness_status},
                )
            latest_jd = self.db.scalar(
                select(JobDescriptionVersion)
                .where(JobDescriptionVersion.job_id == job.id)
                .order_by(JobDescriptionVersion.version_number.desc())
                .limit(1)
                .with_for_update()
            )
            if (
                latest_jd is None
                or latest_jd.completeness_status != "COMPLETE"
                or not has_substantive_job_description(latest_jd.content)
            ):
                raise DomainError(
                    "JOB_DESCRIPTION_NOT_COMPLETE",
                    "A current, substantively complete job description is required before application approval.",
                    409,
                    {"completeness_status": latest_jd.completeness_status if latest_jd else "MISSING"},
                )
            route_verification = current_application_route_verification(self.db, job)
            if route_verification.get("status") != "ACTIVE":
                raise DomainError(
                    "APPLICATION_ROUTE_NOT_VERIFIED",
                    "The current application route must have a fresh successful verification before approval.",
                    409,
                    {"route_status": route_verification.get("status")},
                )

        submission_package = None
        if target_status in {"APPROVED", "SUBMITTED"}:
            latest_package = self.db.scalar(
                select(ApplicationPackage)
                .where(ApplicationPackage.application_id == application.id)
                .order_by(ApplicationPackage.version_number.desc())
                .limit(1)
                .with_for_update()
            )
            if latest_package is None or latest_package.status != "APPROVED":
                raise DomainError(
                    "APPLICATION_PACKAGE_NOT_APPROVED",
                    "The latest application package must be approved before application approval or submission can be recorded.",
                    409,
                    {"latest_package_status": latest_package.status if latest_package else None},
                )
            package_items = list(self.db.scalars(
                select(ApplicationPackageItem)
                .where(ApplicationPackageItem.package_id == latest_package.id)
                .order_by(ApplicationPackageItem.artifact_type)
                .with_for_update()
            ).all())
            blockers = ApplicationPackageReviewService(self.db).readiness(
                profile.id, latest_package.id, package_items, user_id
            )
            if blockers:
                raise DomainError(
                    "APPLICATION_PACKAGE_NOT_READY",
                    "The approved application package no longer passes readiness checks.",
                    409,
                    {"blockers": blockers},
                )
            if target_status == "APPROVED":
                job = self.db.get(Job, application.job_id)
                latest_jd = self.db.scalar(
                    select(JobDescriptionVersion)
                    .where(JobDescriptionVersion.job_id == application.job_id)
                    .order_by(JobDescriptionVersion.version_number.desc())
                    .limit(1)
                )
                snapshot = latest_package.source_snapshot if isinstance(latest_package.source_snapshot, dict) else {}
                snapshot_job = snapshot.get("job") if isinstance(snapshot.get("job"), dict) else {}
                snapshot_jd = snapshot.get("job_description") if isinstance(snapshot.get("job_description"), dict) else None
                if (
                    job is None
                    or snapshot_job.get("revision") != job.revision
                    or snapshot_job.get("application_url") != job.application_url
                    or snapshot_job.get("title") != job.canonical_title
                    or snapshot_job.get("company") != job.company_name
                    or snapshot_job.get("location") != {
                        "country": job.location_country,
                        "region": job.location_region,
                        "city": job.location_city,
                    }
                    or snapshot_job.get("work_mode") != job.work_mode
                    or snapshot_job.get("remote_eligibility") != job.remote_eligibility
                    or latest_jd is None
                    or snapshot_jd is None
                    or snapshot_jd.get("id") != str(latest_jd.id)
                    or snapshot_jd.get("content_hash") != latest_jd.content_hash
                ):
                    raise DomainError(
                        "APPLICATION_PACKAGE_SOURCE_STALE",
                        "The approved package must reflect the current job and job-description version.",
                        409,
                    )
            submission_package = latest_package

        now = datetime.now(timezone.utc)
        previous = application.status
        try:
            event_data = transition_application(
                application,
                target_status,
                actor_type="USER",
                reason=reason,
                notes=notes,
                now=now,
            )
        except ValueError as exc:
            raise DomainError("INVALID_STATE_TRANSITION", str(exc), 409) from exc
        application_event = ApplicationEvent(
            application_id=application.id,
            event_type=event_data["event_type"],
            from_status=previous,
            to_status=application.status,
            actor_type="USER",
            actor_id=profile.user_id,
            occurred_at=now,
            reason=event_data["reason"],
            notes=event_data["notes"],
            metadata_json={
                "source": (
                    "CANDIDATE_ACTION" if target_status == "APPROVED"
                    else "CANDIDATE_RECORDED"
                ),
                **({
                    "application_package_id": str(submission_package.id),
                    "application_package_version": submission_package.version_number,
                } if submission_package is not None else {}),
            },
        )
        self.db.add(application_event)
        self.db.flush()
        if application_event.event_type == "APPLICATION_STATUS_CHANGED":
            self.db.add(OutboxEvent(
                event_type="APPLICATION_STATUS_CHANGED",
                aggregate_type="Application",
                aggregate_id=application.id,
                payload={"application_event_id": str(application_event.id)},
                status="PENDING",
                attempt_count=0,
                available_at=now,
            ))
        self.db.commit()
        self.db.refresh(application)
        return application


    def list_application_audit_events(self, user_id: UUID, application_id: UUID) -> list[AuditEvent]:
        profile = self._profile(user_id)
        application = self.db.scalar(
            select(Application).where(
                Application.id == application_id,
                Application.candidate_profile_id == profile.id,
            )
        )
        if application is None:
            raise DomainError("APPLICATION_NOT_FOUND", "Application not found", 404)
        return AuditEventService(self.db).list_for_resource("application", application.id)

    def override_evaluation(
        self,
        user_id: UUID,
        evaluation_id: UUID,
        new_disposition: str,
        expected_revision: int,
        reason: str,
        notes: str | None = None,
    ) -> CandidateEvaluationOverride:
        """Append a candidate-owned disposition override without changing the evaluation."""
        profile = self._profile(user_id)
        profile = self.db.scalar(
            select(CandidateProfile).where(CandidateProfile.id == profile.id).with_for_update()
        )
        evaluation = self.db.scalar(
            select(CandidateEvaluation).where(
                CandidateEvaluation.id == evaluation_id,
                CandidateEvaluation.candidate_profile_id == profile.id,
            ).with_for_update()
        )
        if evaluation is None:
            raise DomainError("EVALUATION_NOT_FOUND", "Evaluation not found", 404)
        if evaluation.validity_status != "CURRENT" or not self._evaluation_inputs_are_current(
            profile, evaluation, datetime.now(timezone.utc)
        ):
            raise DomainError("EVALUATION_STALE", "Re-evaluate before overriding a stale recommendation.", 409)
        latest = self.db.scalar(
            select(CandidateEvaluationOverride)
            .where(CandidateEvaluationOverride.evaluation_id == evaluation.id)
            .order_by(CandidateEvaluationOverride.revision.desc())
            .limit(1)
            .with_for_update()
        )
        current_revision = latest.revision if latest else 0
        if expected_revision != current_revision:
            raise DomainError(
                "REVISION_CONFLICT", "Override history changed; reload before submitting.", 409,
                {"current_revision": current_revision},
            )
        if new_disposition not in {"APPLY", "REVIEW", "WATCH", "REJECT"}:
            raise DomainError("INVALID_DISPOSITION", "Unsupported candidate disposition.", 422)
        if new_disposition == "APPLY" and evaluation.hard_blockers:
            raise DomainError(
                "HARD_BLOCKER_OVERRIDE_FORBIDDEN",
                "A candidate override cannot bypass deterministic hard blockers.", 409,
                {"hard_blockers": evaluation.hard_blockers},
            )
        row = CandidateEvaluationOverride(
            evaluation_id=evaluation.id,
            candidate_profile_id=profile.id,
            actor_user_id=user_id,
            revision=current_revision + 1,
            original_decision=evaluation.outcome,
            new_disposition=new_disposition,
            reason=reason.strip(),
            notes=notes.strip() if notes else None,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def list_evaluation_overrides(
        self, user_id: UUID, evaluation_id: UUID
    ) -> list[CandidateEvaluationOverride]:
        profile = self._profile(user_id)
        evaluation = self.db.scalar(
            select(CandidateEvaluation).where(
                CandidateEvaluation.id == evaluation_id,
                CandidateEvaluation.candidate_profile_id == profile.id,
            )
        )
        if evaluation is None:
            raise DomainError("EVALUATION_NOT_FOUND", "Evaluation not found", 404)
        return list(self.db.scalars(
            select(CandidateEvaluationOverride)
            .where(
                CandidateEvaluationOverride.evaluation_id == evaluation.id,
                CandidateEvaluationOverride.candidate_profile_id == profile.id,
            )
            .order_by(CandidateEvaluationOverride.revision)
        ).all())
