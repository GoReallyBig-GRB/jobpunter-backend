"""Candidate-specific evaluation of canonical jobs collected by a discovery mission.

Only persisted QUALIFIED evaluations count toward the target. Missing verification,
stale observations, incomplete descriptions, and unavailable requirement assessments
remain review items; the worker never invents a semantic match.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.opportunities.evaluation import evaluate_candidate
from app.infrastructure.db.models import (
    CandidateClaim,
    CandidateEvaluation,
    DiscoveryMission,
    DiscoveryMissionSourceRun,
    Evidence,
    Job,
    JobDescriptionVersion,
    JobProvenance,
    JobRequirement,
    JobResolution,
    RawSourceRecord,
    WorkItem,
)


@dataclass(frozen=True)
class ScreeningResult:
    eligible_for_matching: bool
    outcome: str
    reasons: tuple[str, ...]


def screen_job_for_discovery(
    job: Any,
    description: Any | None,
    *,
    now: datetime,
    max_job_age_days: int,
    verification_required: bool,
) -> ScreeningResult:
    """Apply deterministic evidence-quality gates before semantic matching."""
    reasons: list[str] = []
    observed_at = job.last_observed_at
    if observed_at is None:
        reasons.append("OBSERVATION_TIME_MISSING")
    else:
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        if observed_at < now - timedelta(days=max_job_age_days):
            reasons.append("JOB_STALE")

    if job.identity_status != "CANONICAL":
        reasons.append("JOB_IDENTITY_NOT_CANONICAL")
    if not (job.canonical_title or "").strip():
        reasons.append("JOB_TITLE_MISSING")
    if not (job.application_url or "").strip():
        reasons.append("APPLICATION_URL_MISSING")

    if verification_required:
        verified_at = job.last_verified_at
        if verified_at is None:
            reasons.append("JOB_NOT_VERIFIED")
        else:
            if verified_at.tzinfo is None:
                verified_at = verified_at.replace(tzinfo=timezone.utc)
            if verified_at < now - timedelta(days=max_job_age_days):
                reasons.append("VERIFICATION_STALE")

    if description is None:
        reasons.append("JOB_DESCRIPTION_MISSING")
    elif str(description.completeness_status).upper() != "USABLE":
        reasons.append("JOB_DESCRIPTION_INCOMPLETE")

    blocking_reasons = {
        "JOB_STALE",
        "JOB_IDENTITY_NOT_CANONICAL",
        "JOB_TITLE_MISSING",
    }
    if any(reason in blocking_reasons for reason in reasons):
        return ScreeningResult(False, "NOT_QUALIFIED", tuple(reasons))
    if reasons:
        return ScreeningResult(False, "NEEDS_REVIEW", tuple(reasons))
    return ScreeningResult(True, "NEEDS_REVIEW", ())


AssessmentProvider = Callable[
    [list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], Job],
    Iterable[Mapping[str, Any]],
]


def _role_key(claims: list[CandidateClaim]) -> str:
    """Use a role key only when the candidate explicitly supplied one."""
    keys = {
        str(claim.claim_content.get("role_key")).strip()
        for claim in claims
        if isinstance(claim.claim_content, dict)
        and isinstance(claim.claim_content.get("role_key"), str)
        and claim.claim_content.get("role_key").strip()
    }
    return sorted(keys)[0] if len(keys) == 1 else "default"


def _latest_descriptions(db: Session, job_ids: list[UUID]) -> dict[UUID, JobDescriptionVersion]:
    if not job_ids:
        return {}
    rows = db.scalars(
        select(JobDescriptionVersion)
        .where(JobDescriptionVersion.job_id.in_(job_ids))
        .order_by(JobDescriptionVersion.job_id, JobDescriptionVersion.version_number.desc())
    ).all()
    latest: dict[UUID, JobDescriptionVersion] = {}
    for row in rows:
        latest.setdefault(row.job_id, row)
    return latest


def evaluate_discovery_mission(
    db: Session,
    mission_id: UUID,
    *,
    matcher: AssessmentProvider | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate canonical jobs for one mission and persist the truthful final counts.

    `matcher` must return explicit requirement assessments backed by candidate
    evidence IDs. Without a matcher, eligible jobs are saved as NEEDS_REVIEW.
    """
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    mission = db.scalar(
        select(DiscoveryMission).where(DiscoveryMission.id == mission_id).with_for_update()
    )
    if mission is None:
        raise ValueError("discovery mission does not exist")
    if mission.status in {"TARGET_MET", "EXHAUSTED", "FAILED", "CANCELLED"}:
        return {
            "mission_id": str(mission.id),
            "status": mission.status,
            "usable_opportunities": mission.cumulative_usable_opportunities,
            "replayed": True,
        }
    if mission.status != "AWAITING_EVALUATION":
        raise ValueError("mission is not awaiting evaluation")

    source_runs = db.scalars(
        select(DiscoveryMissionSourceRun).where(
            DiscoveryMissionSourceRun.mission_id == mission.id,
            DiscoveryMissionSourceRun.acquisition_run_id.is_not(None),
        )
    ).all()
    acquisition_run_ids = [row.acquisition_run_id for row in source_runs if row.acquisition_run_id]
    job_ids: set[UUID] = set()
    if acquisition_run_ids:
        job_ids = set(db.scalars(
            select(JobResolution.job_id)
            .join(RawSourceRecord, RawSourceRecord.id == JobResolution.raw_source_record_id)
            .where(
                RawSourceRecord.acquisition_run_id.in_(acquisition_run_ids),
                JobResolution.job_id.is_not(None),
                JobResolution.status.in_({"CREATED", "MATCHED"}),
            )
        ).all())

    candidate_claims = db.scalars(
        select(CandidateClaim).where(
            CandidateClaim.candidate_profile_id == mission.candidate_profile_id
        ).order_by(CandidateClaim.created_at, CandidateClaim.id)
    ).all()
    candidate_evidence = db.scalars(
        select(Evidence).where(Evidence.candidate_profile_id == mission.candidate_profile_id)
        .order_by(Evidence.created_at, Evidence.id)
    ).all()
    evidence_payload = [
        {
            "id": str(item.id),
            "status": item.status,
            "expires_at": item.expires_at,
            "evidence_type": item.evidence_type,
            "content": item.content,
        }
        for item in candidate_evidence
    ]
    claims_payload = [
        {"id": str(item.id), "claim_type": item.claim_type, "claim_content": item.claim_content,
         "status": item.status, "effective_from": item.effective_from, "effective_to": item.effective_to}
        for item in candidate_claims
    ]
    target_role_key = _role_key(candidate_claims)
    policy = mission.policy_snapshot or {}
    max_age_days = int(policy.get("max_job_age_days", 21))
    verification_required = bool(policy.get("verification_required", True))
    descriptions = _latest_descriptions(db, sorted(job_ids))
    jobs = db.scalars(select(Job).where(Job.id.in_(job_ids)).order_by(Job.id)).all() if job_ids else []

    evaluated = 0
    qualified_ids: set[UUID] = set()
    review_count = 0
    not_qualified_count = 0
    for job in jobs:
        description = descriptions.get(job.id)
        screen = screen_job_for_discovery(
            job, description, now=current_time, max_job_age_days=max_age_days,
            verification_required=verification_required,
        )
        requirements: list[dict[str, Any]] = []
        if description is not None:
            requirement_rows = db.scalars(
                select(JobRequirement).where(
                    JobRequirement.job_description_version_id == description.id
                ).order_by(JobRequirement.id)
            ).all()
            requirements = [
                {"id": str(item.id), "description": item.description,
                 "hard_constraint": item.hard_constraint, "importance": item.importance,
                 "extraction_status": item.extraction_status}
                for item in requirement_rows
            ]

        if not screen.eligible_for_matching:
            outcome = screen.outcome
            score = None
            blockers = [{"reason": reason} for reason in screen.reasons]
            evidence_summary = {
                "approved_evidence_ids": [],
                "rejected_evidence_ids": [],
                "requirements_total": len(requirements),
                "requirements_matched": 0,
                "requirements_unmatched": 0,
                "requirements_unresolved": len(requirements),
            }
            explanation = {
                "screening_reasons": list(screen.reasons),
                "matching_performed": False,
            }
            policy_version = "discovery-screen-v1"
        elif not requirements:
            outcome, score, blockers = "NEEDS_REVIEW", None, []
            evidence_summary = {
                "approved_evidence_ids": [], "rejected_evidence_ids": [],
                "requirements_total": 0, "requirements_matched": 0,
                "requirements_unmatched": 0, "requirements_unresolved": 0,
            }
            explanation = {"screening_reasons": [], "matching_performed": False,
                           "review_reason": "JOB_REQUIREMENTS_NOT_EXTRACTED"}
            policy_version = "discovery-screen-v1"
        elif matcher is None:
            outcome, score, blockers = "NEEDS_REVIEW", None, []
            evidence_summary = {
                "approved_evidence_ids": [], "rejected_evidence_ids": [],
                "requirements_total": len(requirements), "requirements_matched": 0,
                "requirements_unmatched": 0, "requirements_unresolved": len(requirements),
            }
            explanation = {"screening_reasons": [], "matching_performed": False,
                           "review_reason": "NO_REQUIREMENT_MATCHER_CONFIGURED"}
            policy_version = "discovery-screen-v1"
        else:
            assessments = list(matcher(requirements, claims_payload, evidence_payload, job))
            result = evaluate_candidate(
                requirements, assessments, evidence_payload, now=current_time
            )
            outcome, score, blockers = result.outcome, result.score, result.hard_blockers
            evidence_summary, explanation = result.evidence_summary, result.explanation
            policy_version = result.policy_version

        existing = db.scalar(select(CandidateEvaluation).where(
            CandidateEvaluation.candidate_profile_id == mission.candidate_profile_id,
            CandidateEvaluation.job_id == job.id,
            CandidateEvaluation.target_role_key == target_role_key,
            CandidateEvaluation.evaluation_version == 1,
        ).with_for_update())
        if existing is None:
            existing = CandidateEvaluation(
                candidate_profile_id=mission.candidate_profile_id,
                job_id=job.id,
                target_role_key=target_role_key,
                evaluation_version=1,
                validity_status="CURRENT",
                outcome=outcome,
                score=score,
                hard_blockers=blockers,
                evidence_summary=evidence_summary,
                explanation=explanation,
                policy_version=policy_version,
                evaluated_at=current_time,
                created_at=current_time,
            )
            db.add(existing)
        else:
            existing.validity_status = "CURRENT"
            existing.outcome = outcome
            existing.score = score
            existing.hard_blockers = blockers
            existing.evidence_summary = evidence_summary
            existing.explanation = explanation
            existing.policy_version = policy_version
            existing.evaluated_at = current_time
        evaluated += 1
        if outcome == "QUALIFIED":
            qualified_ids.add(job.id)
        elif outcome == "NEEDS_REVIEW":
            review_count += 1
        else:
            not_qualified_count += 1

    # Counts are distinct canonical jobs across every source, not raw records.
    mission.cumulative_usable_opportunities = len(qualified_ids)
    target = int(policy.get("target_opportunities", 20))
    mission.status = "TARGET_MET" if len(qualified_ids) >= target else "EXHAUSTED"
    mission.completed_at = current_time
    mission.next_attempt_at = None
    mission.error_code = None

    for source_run in source_runs:
        if source_run.acquisition_run_id is None:
            source_run.usable_count = 0
            continue
        per_run_ids = set(db.scalars(
            select(JobResolution.job_id)
            .join(RawSourceRecord, RawSourceRecord.id == JobResolution.raw_source_record_id)
            .where(
                RawSourceRecord.acquisition_run_id == source_run.acquisition_run_id,
                JobResolution.job_id.in_(qualified_ids) if qualified_ids else False,
                JobResolution.status.in_({"CREATED", "MATCHED"}),
            )
        ).all())
        source_run.usable_count = len(per_run_ids)

    work_item = db.scalar(select(WorkItem).where(
        WorkItem.work_type == "DISCOVERY_MISSION",
        WorkItem.resource_id == mission.id,
    ).with_for_update())
    if work_item is not None:
        work_item.status = "COMPLETED"
        work_item.completed_at = current_time
        work_item.error_code = None
        work_item.result = {
            "mission_status": mission.status,
            "jobs_evaluated": evaluated,
            "qualified_jobs": len(qualified_ids),
            "needs_review": review_count,
            "not_qualified": not_qualified_count,
            "usable_opportunities": len(qualified_ids),
            "target_opportunities": target,
        }
        work_item.updated_at = current_time
    db.commit()
    return {
        "mission_id": str(mission.id),
        "status": mission.status,
        "jobs_evaluated": evaluated,
        "qualified_jobs": len(qualified_ids),
        "needs_review": review_count,
        "not_qualified": not_qualified_count,
        "usable_opportunities": len(qualified_ids),
    }
