"""Persistence orchestration for candidate-specific job evaluations."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.candidates.service import CandidateService
from app.application.opportunities.evaluation_policy import evaluate_candidate_job
from app.domain.candidate.claim_support import claim_facts_are_supported
from app.application.opportunities.semantic_matching import match_requirements_to_evidence
from app.infrastructure.db.models import (
    CandidateClaim, CandidateClaimEvidence, CandidateEvaluation, CandidatePreferences, CandidateProfile, Evidence, Job,
    JobDescriptionVersion,
)


class CandidateEvaluationService:
    def __init__(self, db: Session):
        self.db = db

    async def evaluate(self, user_id: UUID, job_id: UUID, target_role_key: str) -> CandidateEvaluation:
        profile = CandidateService(self.db).get_or_create_profile(user_id)
        # Serialize evaluation-version allocation for this candidate. Locking only the
        # latest evaluation cannot protect the "no prior row" case under concurrency.
        profile = self.db.scalar(
            select(CandidateProfile).where(CandidateProfile.id == profile.id).with_for_update()
        )
        job = self.db.get(Job, job_id)
        if job is None:
            raise DomainError("JOB_NOT_FOUND", "Job not found", 404)

        jd_version = self.db.scalar(
            select(JobDescriptionVersion)
            .where(JobDescriptionVersion.job_id == job.id)
            .order_by(JobDescriptionVersion.version_number.desc())
            .limit(1)
        )
        preferences_row = self.db.scalar(
            select(CandidatePreferences).where(CandidatePreferences.candidate_profile_id == profile.id)
        )
        preferences = preferences_row.preferences if preferences_row else {}
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
        }
        evaluation_preferences = dict(preferences)
        # The requested role pathway scopes this evaluation; saved target roles remain
        # persisted preferences but must not silently override the explicit request.
        evaluation_preferences["targetRoles"] = [
            target_role_key.replace("-", " ").replace("_", " ")
        ]
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
        }
        result = evaluate_candidate_job(**policy_args)
        ai_trace: list[dict] = []
        semantic_needed = any("No validated semantic requirement-to-evidence assessment is available" in str(reason) for reason in result.get("explanation", {}).get("reasons", []))
        if semantic_needed:
            semantic_matches, ai_trace, failure_reason = await match_requirements_to_evidence(
                db=self.db, user_id=user_id, job=job_payload,
                description=policy_args["job_description"], approved_evidence=all_evidence_payload,
            )
            if semantic_matches:
                result = evaluate_candidate_job(**policy_args, semantic_matches=semantic_matches)
            else:
                result["outcome"] = "REVIEW"
                result["score"] = None
                result["explanation"]["confidence"] = "LOW"
                result["explanation"]["reasons"].append("Semantic evaluation did not produce a validated assessment; no application recommendation was inferred.")
                result["explanation"]["gaps"].append(failure_reason or "Retry semantic evaluation after correcting the source or AI configuration.")
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
                "approved_claims": claim_payload,
                "excluded_claims": excluded_claims,
                "preferences_revision": preferences_row.revision if preferences_row else 0,
                "preferences": preferences,
                "evaluation_target_role_key": target_role_key,
                "job_id": str(job.id),
                "job_revision": job.revision,
                "job_snapshot": job_payload,
                "job_description_version_id": str(jd_version.id) if jd_version else None,
                "job_description_version_number": jd_version.version_number if jd_version else None,
                "job_description_content_hash": jd_version.content_hash if jd_version else None,
                "job_description_completeness_status": jd_version.completeness_status if jd_version else "MISSING",
                "evaluation_policy_version": result["policy_version"],
                "ai_trace": ai_trace,
            },
            evaluated_at=now,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row
