"""Persistence orchestration for candidate-specific job evaluations."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.candidates.service import CandidateService
from app.application.ai.output_contracts import get_task_output_contract_version
from app.application.jobs.access import candidate_can_access_job
from app.application.jobs.job_verification import current_application_route_verification
from app.application.jobs.requirement_snapshot import (
    snapshot_job_requirements,
    snapshot_requirement_extraction_trace,
)
from app.application.opportunities.evaluation_policy import (
    DEFAULT_DECISION_RULES,
    POLICY_VERSION,
    evaluate_candidate_job,
    validate_decision_rules,
)
from app.domain.candidate.claim_support import claim_facts_are_supported
from app.application.opportunities.semantic_matching import match_requirements_to_evidence
from app.infrastructure.db.models import (
    CandidateClaim, CandidateClaimEvidence, CandidateEvaluation, CandidatePreferences, CandidateProfile, CareerPathway, Evidence, Job,
    JobDescriptionVersion,
    GovernanceConfigVersion, OutboxEvent,
)


def _merge_preference_terms(
    preferences: dict,
    direct_keys: tuple[str, ...],
    nested_key: str,
) -> list[str]:
    """Combine supported flat and nested terminology preferences without inventing values."""
    values: list[str] = []
    for key in direct_keys:
        supplied = preferences.get(key)
        if isinstance(supplied, list):
            values.extend(
                item.strip() for item in supplied
                if isinstance(item, str) and item.strip()
            )
    nested = preferences.get("terminologyPreferences", preferences.get("terminology_preferences"))
    if isinstance(nested, dict):
        supplied = nested.get(nested_key)
        if isinstance(supplied, list):
            values.extend(
                item.strip() for item in supplied
                if isinstance(item, str) and item.strip()
            )
    return list(dict.fromkeys(values))

def _merge_unique_preference_values(existing: object, additions: list[str]) -> list[str]:
    """Merge saved and approved-pathway soft preferences without duplicates."""
    values = existing if isinstance(existing, list) else []
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*values, *additions]:
        if not isinstance(item, str) or not item.strip():
            continue
        normalized = item.strip()
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return merged


class CandidateEvaluationService:
    def __init__(self, db: Session):
        self.db = db

    async def evaluate(
        self, user_id: UUID, job_id: UUID, target_role_key: str, *, commit: bool = True
    ) -> CandidateEvaluation:
        profile = CandidateService(self.db).get_or_create_profile(user_id)
        # Serialize evaluation-version allocation for this candidate. Locking only the
        # latest evaluation cannot protect the "no prior row" case under concurrency.
        profile = self.db.scalar(
            select(CandidateProfile).where(CandidateProfile.id == profile.id).with_for_update()
        )
        job = self.db.get(Job, job_id)
        if job is None or not candidate_can_access_job(self.db, user_id, job_id):
            # Return the same response for missing and inaccessible jobs to avoid
            # revealing private manual submissions by UUID.
            raise DomainError("JOB_NOT_FOUND", "Job not found", 404)

        jd_version = self.db.scalar(
            select(JobDescriptionVersion)
            .where(JobDescriptionVersion.job_id == job.id)
            .order_by(JobDescriptionVersion.version_number.desc())
            .limit(1)
        )
        decision_rule_config = self.db.scalar(
            select(GovernanceConfigVersion).where(
                GovernanceConfigVersion.config_key == "decision.rules.candidate_evaluation",
                GovernanceConfigVersion.status == "PUBLISHED",
            )
        )
        decision_rules = validate_decision_rules(
            decision_rule_config.payload if decision_rule_config else DEFAULT_DECISION_RULES
        )
        decision_rule_config_id = (
            str(decision_rule_config.id) if decision_rule_config else None
        )
        decision_rule_config_version = (
            decision_rule_config.version if decision_rule_config else None
        )
        decision_rule_config_fingerprint = (
            decision_rule_config.payload_fingerprint if decision_rule_config else "builtin"
        )
        decision_policy_version = (
            f"{POLICY_VERSION}+rules-v{decision_rule_config.version}"
            if decision_rule_config else POLICY_VERSION
        )
        job_requirements_snapshot = snapshot_job_requirements(
            self.db, jd_version.id if jd_version else None,
        )
        preferences_row = self.db.scalar(
            select(CandidatePreferences).where(CandidatePreferences.candidate_profile_id == profile.id)
        )
        preferences = preferences_row.preferences if preferences_row else {}
        approved_pathways = self.db.scalars(select(CareerPathway).where(
            CareerPathway.candidate_profile_id == profile.id,
            CareerPathway.approval_status == "APPROVED",
        ).order_by(CareerPathway.id)).all()
        normalize_role = lambda value: "-".join(
            part for part in re.split(r"[^a-z0-9]+", value.strip().casefold()) if part
        )
        matching_pathways = [
            pathway
            for pathway in approved_pathways
            if any(
                normalize_role(role) == normalize_role(target_role_key)
                for role in pathway.target_roles
                if isinstance(role, str) and role.strip()
            )
        ]
        pathway_snapshot = [
            {
                "id": str(pathway.id),
                "revision": pathway.revision,
                "title": pathway.title,
                "role_family": pathway.role_family,
                "target_roles": pathway.target_roles,
                "target_seniority": pathway.target_seniority,
                "domains": pathway.domains,
            }
            for pathway in matching_pathways
        ]
        now = datetime.now(timezone.utc)
        evidence_rows = list(self.db.scalars(
            select(Evidence).where(
                Evidence.candidate_profile_id == profile.id,
                Evidence.status == "APPROVED",
            ).order_by(Evidence.updated_at.desc())
        ).all())
        evidence_rows = [
            row for row in evidence_rows
            if (row.effective_from is None or row.effective_from <= now)
            and (row.effective_to is None or row.effective_to > now)
            and (row.expires_at is None or row.expires_at > now)
        ]
        # evidenceExclusions means exact Evidence row IDs only. Excluded records remain
        # in the Evidence Library but are omitted from this evaluation and claim support.
        raw_exclusions = preferences.get("evidenceExclusions", preferences.get("evidence_exclusions", []))
        excluded_evidence_ids = {str(item) for item in raw_exclusions if isinstance(item, str)}
        evidence_rows = [row for row in evidence_rows if str(row.id) not in excluded_evidence_ids]
        claims = list(self.db.scalars(
            select(CandidateClaim).where(
                CandidateClaim.candidate_profile_id == profile.id,
                CandidateClaim.status == "APPROVED",
            )
        ).all())
        claims = [
            row for row in claims
            if (row.effective_from is None or row.effective_from <= now)
            and (row.effective_to is None or row.effective_to > now)
        ]
        current_evidence_by_id = {row.id: row for row in evidence_rows}
        claim_support_rows = list(self.db.scalars(
            select(CandidateClaimEvidence)
            .where(CandidateClaimEvidence.candidate_claim_id.in_([row.id for row in claims]))
        ).all()) if claims else []
        support_ids_by_claim: dict[UUID, list[UUID]] = {}
        for link in claim_support_rows:
            support_ids_by_claim.setdefault(link.candidate_claim_id, []).append(link.evidence_id)

        eligible_claims = []
        excluded_claims = []
        for claim in claims:
            current_support = [
                current_evidence_by_id[evidence_id]
                for evidence_id in support_ids_by_claim.get(claim.id, [])
                if evidence_id in current_evidence_by_id
            ]
            if current_support and claim_facts_are_supported(
                claim.claim_content,
                [item.content for item in current_support],
            ):
                eligible_claims.append(claim)
            else:
                excluded_claims.append({
                    "id": str(claim.id),
                    "type": claim.claim_type,
                    "revision": claim.revision,
                    "content": claim.claim_content,
                    "reason": "NO_CURRENT_APPROVED_SUPPORTING_EVIDENCE",
                })
        claims = eligible_claims
        evidence_payload = [
            {"id": str(row.id), "type": row.evidence_type, "revision": row.revision, "content": row.content}
            for row in evidence_rows
        ]
        claim_payload = [
            {"id": str(row.id), "type": row.claim_type, "revision": row.revision, "content": row.claim_content}
            for row in claims
        ]
        job_payload = {
            "id": str(job.id), "canonical_title": job.canonical_title, "company_name": job.company_name,
            "employment_type": job.employment_type, "lifecycle_status": job.lifecycle_status,
            "salary_min": float(job.salary_min) if job.salary_min is not None else None,
            "salary_max": float(job.salary_max) if job.salary_max is not None else None,
            "salary_currency": job.salary_currency, "salary_period": job.salary_period,
            "location_country": job.location_country,
            "work_mode": job.work_mode, "location_restrictions": job.location_restrictions,
            "metadata": job.metadata_json or {}, "freshness_status": job.freshness_status,
            "application_url": job.application_url,
            "application_route_verification": current_application_route_verification(
                self.db, job, now=now,
            ),
        }
        evaluation_preferences = dict(preferences)
        # The explicit evaluation role scopes this evaluation. Approved pathway
        # family/domain/seniority values enrich soft matching only; they never
        # replace or weaken saved hard exclusions and eligibility constraints.
        evaluation_preferences["targetRoles"] = [
            target_role_key.replace("-", " ").replace("_", " ")
        ]
        evaluation_preferences["targetRoleFamilies"] = _merge_unique_preference_values(
            preferences.get("targetRoleFamilies") or preferences.get("target_role_families"),
            [pathway.role_family for pathway in matching_pathways],
        )
        evaluation_preferences["targetDomains"] = _merge_unique_preference_values(
            preferences.get("targetDomains") or preferences.get("target_domains"),
            [domain for pathway in matching_pathways for domain in pathway.domains],
        )
        evaluation_preferences["targetSeniority"] = _merge_unique_preference_values(
            preferences.get("targetSeniority") or preferences.get("target_seniority"),
            [seniority for pathway in matching_pathways for seniority in pathway.target_seniority],
        )
        all_evidence_payload = evidence_payload + [
            {"id": item["id"], "type": item["type"], "content": item["content"]}
            for item in claim_payload
        ]
        policy_args = {
            "job": job_payload,
            "job_description": jd_version.content if jd_version else "",
            "description_version_id": str(jd_version.id) if jd_version else None,
            "description_completeness": jd_version.completeness_status if jd_version else "MISSING",
            "candidate_evidence": all_evidence_payload,
            "preferences": evaluation_preferences,
            "candidate_profile_data": profile.profile_data or {},
        }
        result = evaluate_candidate_job(
            **policy_args,
            decision_rules=decision_rules,
            policy_version_override=decision_policy_version,
        )
        ai_trace: list[dict] = []
        # The deterministic policy returns early for hard blockers and for jobs
        # that are not ready for semantic assessment (for example, closed/stale jobs,
        # incomplete descriptions, unresolved eligibility, or unverified application
        # routes). Its explicit semantic-unavailable result is the signal that those
        # gates passed and governed semantic matching may run.
        semantic_needed = any(
            "No validated semantic requirement-to-evidence assessment is available" in str(reason)
            for reason in result.get("explanation", {}).get("reasons", [])
        )
        if semantic_needed:
            semantic_matches, ai_trace, failure_reason = await match_requirements_to_evidence(
                db=self.db, user_id=user_id, job=job_payload,
                description=policy_args["job_description"],
                description_version_id=jd_version.id if jd_version else None,
                requirement_extraction_status=jd_version.requirement_extraction_status if jd_version else None,
                transferability_policy=(
                    preferences.get("transferabilityPolicy")
                    or preferences.get("transferability_policy")
                    or "MODERATE"
                ),
                evidence_emphasis=preferences.get("evidenceEmphasis") or preferences.get("evidence_emphasis") or [],
                preferred_terminology=_merge_preference_terms(
                    preferences, ("preferredTerminology", "preferred_terminology"), "prefer",
                ),
                avoided_terminology=_merge_preference_terms(
                    preferences, ("avoidedTerminology", "avoided_terminology"), "avoid",
                ),
                other_search_constraints=preferences.get("otherSearchConstraints") or preferences.get("other_search_constraints") or [],
                approved_evidence=all_evidence_payload,
            )
            trace = next(
                (
                    item for item in ai_trace
                    if isinstance(item, dict) and item.get("task") == "evidence_matching"
                ),
                None,
            )
            trace_is_governed = False
            if trace is not None:
                try:
                    trace_is_governed = (
                        trace.get("output_contract_version")
                        == get_task_output_contract_version("evidence_matching")
                        and bool(trace.get("config_fingerprint"))
                        and trace.get("config_fingerprint") != "unversioned"
                        and bool(trace.get("task_policy_version"))
                        and bool(trace.get("system_policy_version"))
                    )
                except (KeyError, ValueError):
                    trace_is_governed = False
            if semantic_matches and trace_is_governed:
                result = evaluate_candidate_job(
                    **policy_args,
                    semantic_matches=semantic_matches,
                    decision_rules=decision_rules,
                    policy_version_override=decision_policy_version,
                )
            else:
                result["outcome"] = "REVIEW"
                result["score"] = None
                result["explanation"]["confidence"] = "LOW"
                result["explanation"]["reasons"].append(
                    "Semantic evaluation did not produce a complete, versioned assessment; no application recommendation was inferred."
                )
                result["explanation"]["gaps"].append(
                    failure_reason
                    or "The semantic result lacks required task/config provenance; retry after correcting the AI configuration."
                )
        existing = self.db.scalar(
            select(CandidateEvaluation).where(
                CandidateEvaluation.candidate_profile_id == profile.id,
                CandidateEvaluation.job_id == job.id,
                CandidateEvaluation.target_role_key == target_role_key,
            ).order_by(CandidateEvaluation.evaluation_version.desc()).limit(1)
            .with_for_update()
        )
        version = (existing.evaluation_version + 1) if existing else 1
        row = CandidateEvaluation(
            candidate_profile_id=profile.id,
            job_id=job.id,
            target_role_key=target_role_key,
            evaluation_version=version,
            validity_status="CURRENT",
            outcome=result["outcome"],
            score=result["score"],
            hard_blockers=result["hard_blockers"],
            evidence_summary=result["evidence_summary"],
            explanation=result["explanation"],
            policy_version=result["policy_version"],
            candidate_profile_revision=profile.revision,
            preferences_revision=preferences_row.revision if preferences_row else 0,
            job_description_version_id=jd_version.id if jd_version else None,
            input_snapshot={
                "candidate_profile_revision": profile.revision,
                "candidate_profile_data": profile.profile_data or {},
                "approved_evidence": evidence_payload,
                "evidence_exclusions_applied": sorted(excluded_evidence_ids),
                "approved_claims": claim_payload,
                "excluded_claims": excluded_claims,
                "preferences_revision": preferences_row.revision if preferences_row else 0,
                "preferences": preferences,
                "effective_evaluation_preferences": evaluation_preferences,
                "evaluation_target_role_key": target_role_key,
                "career_pathway_snapshot": pathway_snapshot,
                "job_id": str(job.id),
                "job_revision": job.revision,
                "job_snapshot": job_payload,
                "job_description_version_id": str(jd_version.id) if jd_version else None,
                "job_description_version_number": jd_version.version_number if jd_version else None,
                "job_description_content_hash": jd_version.content_hash if jd_version else None,
                "job_description_completeness_status": jd_version.completeness_status if jd_version else "MISSING",
                "job_description_requirement_extraction_status": jd_version.requirement_extraction_status if jd_version else "MISSING",
                "job_description_requirement_extraction_version": jd_version.requirement_extraction_version if jd_version else None,
                "job_requirements": job_requirements_snapshot,
                "job_requirement_extraction_trace": snapshot_requirement_extraction_trace(
                    self.db, jd_version.id if jd_version else None,
                ),
                "evaluation_policy_version": result["policy_version"],
                "decision_rule_config_version_id": decision_rule_config_id,
                "decision_rule_config_version": decision_rule_config_version,
                "decision_rule_config_fingerprint": decision_rule_config_fingerprint,
                "ai_trace": ai_trace,
            },
            evaluated_at=now,
        )
        self.db.add(row)
        self.db.flush()
        if row.outcome == "REVIEW":
            self.db.add(OutboxEvent(
                event_type="EVALUATION_REQUIRES_ATTENTION",
                aggregate_type="CandidateEvaluation",
                aggregate_id=row.id,
                payload={"evaluation_id": str(row.id)},
                status="PENDING",
                attempt_count=0,
                available_at=now,
            ))
        if commit:
            self.db.commit()
        else:
            # Mission processing owns the surrounding transaction and holds its
            # mission/work-item locks until the state transition is committed.
            self.db.flush()
        self.db.refresh(row)
        return row
