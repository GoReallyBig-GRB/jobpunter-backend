"""Evidence matching against reviewed requirements tied to an immutable JD version."""
from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.ai.schemas import GenerateRequest
from app.application.ai.service import execute_ai_task
from app.infrastructure.db.models import JobRequirement


def _requirement_dimension(requirement_type: str | None, text: str) -> str:
    kind = (requirement_type or "").strip().upper()
    statement = text.casefold()
    if kind in {"LOCATION", "WORK_AUTHORIZATION", "WORK_MODE"} or any(
        term in statement
        for term in ("work authorization", "must be authorized", "visa sponsorship", "eligible to work", "remote", "hybrid")
    ):
        return "location_work_authorization"
    if kind in {"EDUCATION", "CERTIFICATION"} or any(
        term in statement for term in ("degree", "certification", "licensed", "bachelor", "master's", "phd")
    ):
        return "education_fit"
    if kind == "SKILL" or any(
        term in statement
        for term in ("python", "sql", "api", "cloud", "programming", "technical architecture", "software engineering")
    ):
        return "technical_fit"
    if any(term in statement for term in ("senior level", "leadership level", "years of experience", "director-level", "executive-level")):
        return "seniority_fit"
    if any(term in statement for term in ("industry", "domain knowledge", "sector experience", "healthcare", "fintech", "saas")):
        return "domain_fit"
    return "core_role_fit"


def _trace(db: Session, response: Any) -> dict[str, Any]:
    from app.infrastructure.db.models.governance import AIExecution

    execution = db.get(AIExecution, response.execution_id) if response.execution_id else None
    return {
        "task": "evidence_matching",
        "execution_id": str(response.execution_id) if response.execution_id else None,
        "provider": response.provider_used,
        "model": response.model_used,
        "task_policy_version": execution.task_policy_version if execution else None,
        "system_policy_version": execution.system_policy_version if execution else None,
        "output_contract_version": execution.output_contract_version if execution else None,
        "config_version_id": str(execution.config_version_id) if execution and execution.config_version_id else None,
        "config_fingerprint": execution.config_fingerprint if execution else None,
    }


async def match_requirements_to_evidence(
    *,
    db: Session,
    user_id: UUID,
    job: dict[str, Any],
    description: str,
    description_version_id: UUID | None,
    requirement_extraction_status: str | None,
    approved_evidence: list[dict[str, Any]],
    transferability_policy: str = "MODERATE",
    evidence_emphasis: list[str] | None = None,
    preferred_terminology: list[str] | None = None,
    avoided_terminology: list[str] | None = None,
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]], str | None]:
    """Match only reviewed, source-grounded requirements from the exact JD version."""
    if len(description) > 70000:
        return None, [], "The job description exceeds the safe semantic-evaluation input limit."
    if description_version_id is None:
        return None, [], "No versioned job description is available for requirement matching."
    if str(requirement_extraction_status or "").upper() not in {"NEEDS_REVIEW", "COMPLETE"}:
        return None, [], "Requirement extraction is not in a stable reviewable state for this JD version."
    if not approved_evidence:
        return None, [], "No approved candidate evidence is available for requirement matching."

    requirement_rows = list(db.scalars(
        select(JobRequirement)
        .where(JobRequirement.job_description_version_id == description_version_id)
        .order_by(JobRequirement.created_at, JobRequirement.id)
    ).all())
    if not requirement_rows:
        return None, [], "No persisted requirements have been extracted for this job-description version."
    unreviewed = [
        row for row in requirement_rows
        if row.extraction_status not in {"CONFIRMED", "REJECTED"}
    ]
    if unreviewed:
        return None, [], "One or more extracted job requirements still need human review."
    confirmed = [row for row in requirement_rows if row.extraction_status == "CONFIRMED"]
    if not confirmed:
        return None, [], "No confirmed job requirements remain for this job-description version."

    normalized_description = " ".join(description.casefold().split())
    requirements = []
    for row in confirmed:
        excerpt = " ".join((row.source_excerpt or "").casefold().split())
        if not row.extraction_version:
            return None, [], "A confirmed job requirement lacks extraction-version provenance."
        if not excerpt or excerpt not in normalized_description:
            return None, [], "A confirmed job requirement lacks a verifiable source excerpt."
        requirements.append({
            "requirement_id": str(row.id),
            "requirement_text": row.description,
            "mandatory": bool(row.hard_constraint),
            "dimension": _requirement_dimension(row.requirement_type, row.description),
            "requirement_type": row.requirement_type,
            "importance": row.importance,
            "hard_constraint": bool(row.hard_constraint),
            "source_excerpt": row.source_excerpt,
            "extraction_version": row.extraction_version,
        })

    evidence_ids = {str(item["id"]) for item in approved_evidence if item.get("id")}
    traces: list[dict[str, Any]] = []
    try:
        matched = await execute_ai_task(
            GenerateRequest(
                task="evidence_matching",
                prompt=json.dumps({
                    "job_title": job.get("canonical_title"),
                    "employer": job.get("company_name"),
                    "job_description_version_id": str(description_version_id),
                    "requirements": requirements,
                    "approved_candidate_evidence": approved_evidence,
                    "candidate_preferences": {
                        "transferability_policy": transferability_policy,
                        "evidence_emphasis": evidence_emphasis or [],
                        "preferred_terminology": preferred_terminology or [],
                        "avoided_terminology": avoided_terminology or [],
                    },
                    "instruction": (
                        "Assess every persisted, human-confirmed requirement only against supplied approved evidence. "
                        "Return one match per requirement, preserve requirement_id and requirement_text, and use exactly "
                        "one status from DIRECT, STRONG_TRANSFERABLE, TRANSFERABLE, PARTIAL, WEAK_TRANSFERABLE, MISSING, "
                        "CONTRADICTED, REQUIRES_VERIFICATION, DISQUALIFYING. DIRECT, STRONG_TRANSFERABLE, TRANSFERABLE, "
                        "PARTIAL, WEAK_TRANSFERABLE, CONTRADICTED and DISQUALIFYING require supplied approved evidence IDs. "
                        "MISSING must have no evidence references. Never invent evidence IDs, requirements, facts or approval. "
                        "Respect the candidate's transferability policy: STRICT and CONSERVATIVE require close equivalence; "
                        "MODERATE and BALANCED allow credible adjacent evidence when the underlying capability is demonstrated; "
                        "FLEXIBLE and AGGRESSIVE may consider broader transferability but must never relabel weak evidence as direct. "
                        "Evidence emphasis may guide which supplied items are discussed, but must not hide contradictory evidence. "
                        "Preferred and avoided terminology affect wording only, not factual claims or match status. "
                        "A semantic status must not override deterministic eligibility rules."
                    ),
                }, ensure_ascii=False),
            ),
            db=db,
            user_id=user_id,
            commit_before_call=False,
        )
        traces.append(_trace(db, matched))
        output = (matched.parsed_data or {}).get("matches") or []
        by_id = {str(item.get("requirement_id")): item for item in output}
        expected = {item["requirement_id"] for item in requirements}
        valid = len(output) == len(requirements) and len(by_id) == len(output) and set(by_id) == expected
        if valid:
            requirement_by_id = {item["requirement_id"]: item for item in requirements}
            for requirement_id, item in by_id.items():
                requirement = requirement_by_id[requirement_id]
                refs = item.get("evidence_refs", [])
                if (
                    item.get("requirement_text") != requirement["requirement_text"]
                    or any(str(ref) not in evidence_ids for ref in refs)
                ):
                    valid = False
                    break
                if str(item.get("status") or "").upper() == "MISSING" and refs:
                    valid = False
                    break
        if not valid:
            return None, traces, "Semantic matching failed requirement coverage or evidence-provenance validation."

        result = []
        for requirement in requirements:
            item = dict(by_id[requirement["requirement_id"]])
            item.update({
                "mandatory": requirement["mandatory"],
                "dimension": requirement["dimension"],
                "job_requirement_id": requirement["requirement_id"],
                "requirement_type": requirement["requirement_type"],
                "importance": requirement["importance"],
                "hard_constraint": requirement["hard_constraint"],
                "source_excerpt": requirement["source_excerpt"],
                "extraction_version": requirement["extraction_version"],
            })
            result.append(item)
        return result, traces, None
    except Exception:
        return None, traces, "Semantic evaluation is unavailable or failed validation."
