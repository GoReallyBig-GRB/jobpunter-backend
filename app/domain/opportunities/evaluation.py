"""Deterministic policy gate for candidate/job requirement assessments.

The evaluator does not infer semantic matches. A matcher (human or AI) supplies
explicit assessments; this module validates evidence references and applies
the deterministic outcome policy.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


POLICY_VERSION = "deterministic-evaluation-v1"
MATCH_RESULTS = {"MATCH", "NO_MATCH", "UNKNOWN"}
QUALIFIED_SCORE_THRESHOLD = 70.0


@dataclass(frozen=True)
class EvaluationResult:
    outcome: str
    score: float | None
    hard_blockers: list[dict[str, Any]]
    evidence_summary: dict[str, Any]
    explanation: dict[str, Any]
    policy_version: str = POLICY_VERSION


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raise ValueError("expires_at must be a datetime or None")


def evaluate_candidate(
    requirements: Iterable[Mapping[str, Any]],
    assessments: Iterable[Mapping[str, Any]],
    evidence: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> EvaluationResult:
    """Evaluate explicit requirement assessments without allowing AI to decide policy.

    Requirement fields: id, description, hard_constraint, importance,
    extraction_status. Assessment fields: requirement_id, result, evidence_ids,
    rationale. Evidence fields: id, status, expires_at.

    Approved, unexpired evidence is the only evidence accepted as support.
    Missing assessments, unresolved extraction, UNKNOWN results, or references
    to unavailable/unapproved/expired evidence result in NEEDS_REVIEW unless
    a separately established hard-constraint NO_MATCH already disqualifies.
    """
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    requirement_list = list(requirements)
    assessment_list = list(assessments)
    evidence_by_id = {str(item["id"]): item for item in evidence if item.get("id") is not None}
    requirement_by_id = {str(item["id"]): item for item in requirement_list if item.get("id") is not None}

    if len(requirement_by_id) != len(requirement_list):
        raise ValueError("Every requirement must have a unique, non-empty id")
    if len({str(item.get("requirement_id")) for item in assessment_list}) != len(assessment_list):
        raise ValueError("Each requirement may have at most one assessment")
    for assessment in assessment_list:
        if str(assessment.get("requirement_id")) not in requirement_by_id:
            raise ValueError("Assessment references an unknown requirement")
        if assessment.get("result") not in MATCH_RESULTS:
            raise ValueError("Assessment result must be MATCH, NO_MATCH, or UNKNOWN")

    assessments_by_requirement = {
        str(item["requirement_id"]): item for item in assessment_list
    }
    accepted_evidence: set[str] = set()
    rejected_evidence: set[str] = set()
    normalized: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    total_weight = 0.0
    earned_weight = 0.0

    for requirement in requirement_list:
        requirement_id = str(requirement["id"])
        assessment = assessments_by_requirement.get(requirement_id)
        extraction_status = str(requirement.get("extraction_status", "UNKNOWN")).upper()
        description = str(requirement.get("description", ""))
        is_hard = bool(requirement.get("hard_constraint", False))
        importance = str(requirement.get("importance", "MEDIUM")).upper()
        weight = {"CRITICAL": 3.0, "HIGH": 2.0, "MEDIUM": 1.0, "LOW": 0.5}.get(importance, 1.0)
        total_weight += weight

        if extraction_status not in {"COMPLETE", "CONFIRMED", "EXTRACTED"} or assessment is None:
            unresolved.append({"requirement_id": requirement_id, "description": description,
                               "reason": "REQUIREMENT_UNRESOLVED"})
            normalized.append({"requirement_id": requirement_id, "result": "UNKNOWN",
                               "hard_constraint": is_hard, "evidence_ids": []})
            continue

        result = str(assessment["result"]).upper()
        cited_ids = [str(value) for value in assessment.get("evidence_ids", [])]
        valid_ids: list[str] = []
        invalid_ids: list[str] = []
        for evidence_id in cited_ids:
            item = evidence_by_id.get(evidence_id)
            expiry = _as_datetime(item.get("expires_at")) if item else None
            is_approved = item is not None and str(item.get("status", "")).upper() == "APPROVED"
            is_unexpired = expiry is None or expiry > current_time
            if is_approved and is_unexpired:
                valid_ids.append(evidence_id)
                accepted_evidence.add(evidence_id)
            else:
                invalid_ids.append(evidence_id)
                rejected_evidence.add(evidence_id)

        if invalid_ids:
            unresolved.append({"requirement_id": requirement_id, "description": description,
                               "reason": "EVIDENCE_NOT_APPROVED_OR_EXPIRED",
                               "evidence_ids": invalid_ids})
            result = "UNKNOWN"
        elif result == "MATCH" and not valid_ids:
            unresolved.append({"requirement_id": requirement_id, "description": description,
                               "reason": "MATCH_HAS_NO_APPROVED_EVIDENCE"})
            result = "UNKNOWN"
        elif result == "UNKNOWN":
            unresolved.append({"requirement_id": requirement_id, "description": description,
                               "reason": "MATCH_UNCERTAIN"})

        normalized.append({
            "requirement_id": requirement_id,
            "result": result,
            "hard_constraint": is_hard,
            "evidence_ids": valid_ids,
            "rationale": str(assessment.get("rationale", "")),
        })

        if result == "NO_MATCH" and is_hard:
            blockers.append({"requirement_id": requirement_id, "description": description,
                             "reason": "HARD_CONSTRAINT_NOT_MET"})
        if result == "MATCH":
            earned_weight += weight

    score = round((earned_weight / total_weight) * 100, 2) if total_weight else None
    if blockers:
        outcome = "NOT_QUALIFIED"
    elif unresolved:
        outcome = "NEEDS_REVIEW"
    elif score is not None and score >= QUALIFIED_SCORE_THRESHOLD:
        outcome = "QUALIFIED"
    else:
        outcome = "NOT_QUALIFIED"

    return EvaluationResult(
        outcome=outcome,
        score=score,
        hard_blockers=blockers,
        evidence_summary={
            "approved_evidence_ids": sorted(accepted_evidence),
            "rejected_evidence_ids": sorted(rejected_evidence),
            "requirements_total": len(requirement_list),
            "requirements_matched": sum(1 for item in normalized if item["result"] == "MATCH"),
            "requirements_unmatched": sum(1 for item in normalized if item["result"] == "NO_MATCH"),
            "requirements_unresolved": len(unresolved),
        },
        explanation={
            "policy_version": POLICY_VERSION,
            "score_threshold": QUALIFIED_SCORE_THRESHOLD,
            "requirements": normalized,
            "unresolved": unresolved,
        },
    )
