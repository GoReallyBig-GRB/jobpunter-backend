"""Deterministic, explainable candidate-to-job evaluation policy.

This policy intentionally does not call an LLM. Hard constraints take precedence
over evidence scoring; missing evidence is uncertainty, not proof of inability.
"""
from __future__ import annotations

import math
import re
from typing import Any
from app.domain.jobs.completeness import has_substantive_job_description

POLICY_VERSION = "candidate-evaluation-v16"


def _normal(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return " ".join(_normal(item) for item in (value.values() if isinstance(value, dict) else value))
    return str(value).casefold()


def _contains_any(text: str, values: list[str]) -> bool:
    return any(value.strip().casefold() in text for value in values if value and value.strip())


_ROUTE_CLASSIFICATIONS = {
    "DIRECT_EMPLOYER", "FREE_ATS", "FREE_AGGREGATOR",
    "PAID", "BLOCKED", "PAID_OR_BLOCKED", "UNKNOWN",
}


def _route_classification(job: dict[str, Any]) -> str:
    metadata = job.get("metadata")
    if not isinstance(metadata, dict):
        return "UNKNOWN"
    value = metadata.get("application_route_classification") or metadata.get("application_route_type") or "UNKNOWN"
    classification = str(value).strip().upper()
    return classification if classification in _ROUTE_CLASSIFICATIONS else "UNKNOWN"


_MATCH_STATUS_ALIASES = {
    "direct": "DIRECT",
    "strong_transferable": "STRONG_TRANSFERABLE",
    "transferable": "TRANSFERABLE",
    "partial": "PARTIAL",
    "weak_transferable": "WEAK_TRANSFERABLE",
    "missing": "MISSING",
    "contradicted": "CONTRADICTED",
    "unverified": "REQUIRES_VERIFICATION",
    "requires_verification": "REQUIRES_VERIFICATION",
    "disqualifying": "DISQUALIFYING",
}
_SUPPORTED_MATCH_STATES = {"DIRECT", "STRONG_TRANSFERABLE", "TRANSFERABLE"}
_VALID_MATCH_STATES = set(_MATCH_STATUS_ALIASES.values())


def _canonical_match(match: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(match)
    raw_status = str(normalized.get("status") or "").strip().casefold()
    normalized["status"] = _MATCH_STATUS_ALIASES.get(raw_status, raw_status.upper())
    return normalized


def _evaluate_candidate_job_impl(
    *,
    job: dict[str, Any],
    job_description: str,
    description_version_id: str | None,
    description_completeness: str | None,
    candidate_evidence: list[dict[str, Any]],
    preferences: dict[str, Any],
    semantic_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return an explainable evaluation payload without persisting or mutating inputs."""
    blockers: list[dict[str, str]] = []
    review_flags: list[str] = []
    reasons: list[str] = []
    gaps: list[str] = []
    jd = (job_description or "").strip()
    job_text = " ".join((str(job.get("canonical_title") or ""), str(job.get("company_name") or ""), jd)).casefold()
    evidence_text = " ".join(_normal(item.get("content")) for item in candidate_evidence).casefold()
    title = str(job.get("canonical_title") or "")
    job_metadata = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    route_classification = _route_classification(job)
    target_roles = preferences.get("targetRoles") or preferences.get("target_roles") or []
    excluded_industries = preferences.get("excludedIndustries") or preferences.get("excluded_industries") or []
    target_industries = preferences.get("targetIndustries") or preferences.get("target_industries") or []
    eligible_countries = preferences.get("eligibleCountries") or preferences.get("eligible_countries") or []
    authorization_countries = preferences.get("workAuthorizationCountries") or preferences.get("work_authorization_countries") or []
    preferred_locations = preferences.get("preferredLocations") or preferences.get("preferred_locations") or []
    excluded_job_types = preferences.get("excludedJobTypes") or preferences.get("excluded_job_types") or []
    employment_types = preferences.get("employmentTypes") or preferences.get("employment_types") or []
    remote_preference = preferences.get("remotePreference") or preferences.get("remote_preference") or "FLEXIBLE"
    max_fee_configured = "maxApplicationFee" in preferences or "max_application_fee" in preferences
    max_application_fee = preferences.get("maxApplicationFee", preferences.get("max_application_fee"))
    max_application_fee_currency = str(
        preferences.get("maxApplicationFeeCurrency")
        or preferences.get("max_application_fee_currency")
        or ""
    ).strip().upper()
    relocation_preference_configured = (
        "willingToRelocate" in preferences or "willing_to_relocate" in preferences
    )
    if route_classification == "PAID" and not max_fee_configured:
        review_flags.append(
            "The application route is explicitly classified as paid, but the candidate's maximum application-fee preference is not recorded."
        )
    willing_to_relocate = preferences.get("willingToRelocate", preferences.get("willing_to_relocate"))
    salary_min = preferences.get("salaryMin", preferences.get("salary_min"))
    salary_currency = str(preferences.get("salaryCurrency") or preferences.get("salary_currency") or "").strip().upper()
    salary_period = str(preferences.get("salaryPeriod") or preferences.get("salary_period") or "UNSPECIFIED").strip().upper()

    job_industry = str(job_metadata.get("industry") or "")
    job_type = str(job.get("employment_type") or "")
    country = str(job.get("location_country") or "")
    work_mode = str(job.get("work_mode") or "").upper()
    restrictions = job.get("location_restrictions") or {}

    # Salary is a hard constraint only when both sides use comparable units.
    # Unknown or incomparable salary data must be reviewed, never treated as a pass.
    salary_review_gap: str | None = None
    if salary_min is not None:
        job_salary_max = job.get("salary_max")
        job_salary_currency = str(job.get("salary_currency") or "").strip().upper()
        job_salary_period = str(job.get("salary_period") or "").strip().upper()
        if job_salary_max is None:
            salary_review_gap = "Candidate minimum salary is recorded, but the job's maximum salary is not disclosed."
        elif not salary_currency or not job_salary_currency or salary_currency != job_salary_currency:
            salary_review_gap = "Candidate and job salary currencies are missing or incompatible; salary eligibility cannot be compared."
        elif salary_period in {"", "UNSPECIFIED"} or job_salary_period in {"", "UNSPECIFIED"}:
            salary_review_gap = "Candidate and job salary periods are missing or unspecified; salary eligibility cannot be compared."
        elif salary_period != job_salary_period:
            salary_review_gap = "Candidate and job salary periods differ; salary eligibility cannot be compared."
        else:
            try:
                if float(job_salary_max) < float(salary_min):
                    blockers.append({
                        "code": "SALARY_BELOW_MINIMUM",
                        "reason": (
                            f"Recorded job maximum salary ({job_salary_max} {job_salary_currency}/{job_salary_period}) "
                            f"is below the candidate's minimum ({salary_min} {salary_currency}/{salary_period})."
                        ),
                    })
            except (TypeError, ValueError):
                salary_review_gap = "Job salary data is invalid; salary eligibility cannot be compared."

    if job_industry and _contains_any(job_industry.casefold(), [str(x) for x in excluded_industries]):
        blockers.append({"code": "EXCLUDED_INDUSTRY", "reason": f"Job industry '{job_industry}' is explicitly excluded in preferences."})
    if job_type and _contains_any(job_type.casefold(), [str(x) for x in excluded_job_types]):
        blockers.append({"code": "EXCLUDED_JOB_TYPE", "reason": f"Employment type '{job_type}' is explicitly excluded in preferences."})
    if employment_types:
        if not job_type:
            review_flags.append("Candidate employment-type preferences are recorded, but the job employment type is missing.")
        elif not _contains_any(job_type.casefold(), [str(x) for x in employment_types]):
            blockers.append({
                "code": "EMPLOYMENT_TYPE_MISMATCH",
                "reason": f"Recorded employment type '{job_type}' is outside the candidate's allowed employment types.",
            })
    if eligible_countries and not country:
        review_flags.append("Candidate eligible countries are recorded, but the job country is missing.")
    elif country and eligible_countries and not _contains_any(country.casefold(), [str(x) for x in eligible_countries]):
        blockers.append({"code": "COUNTRY_INELIGIBLE", "reason": f"Job country '{country}' is outside the candidate's recorded eligible-country preferences."})
    restricted_countries = restrictions.get("countries") or restrictions.get("eligibleCountries") or []
    if restricted_countries and not country:
        review_flags.append("The job records country restrictions, but the job country is missing.")
    elif restricted_countries and not _contains_any(country.casefold(), [str(x) for x in restricted_countries]):
        blockers.append({"code": "JOB_LOCATION_RESTRICTION", "reason": f"Job location '{country}' conflicts with the job's recorded country restrictions."})
    if re.search(r"work authorization|required authorization|must be authorized|citizen(ship)? required|visa sponsorship (?:is )?not available", jd, re.I):
        if not authorization_countries:
            review_flags.append("The job mentions a work-authorization constraint, but candidate authorization is not recorded.")
        elif not country:
            review_flags.append("The job mentions a work-authorization constraint, but the job country is not recorded.")
        elif not _contains_any(country.casefold(), [str(x) for x in authorization_countries]):
            review_flags.append("The job's work-authorization requirement needs verification against the candidate's recorded authorization.")
    if remote_preference == "REMOTE_ONLY":
        if not work_mode:
            review_flags.append("The candidate requires remote-only work, but the job's work mode is not recorded.")
        elif work_mode not in {"REMOTE", "FULLY_REMOTE"}:
            blockers.append({"code": "REMOTE_ONLY_MISMATCH", "reason": f"Remote-only preference conflicts with recorded work mode '{work_mode}'."})

    # Fee and relocation checks consume explicit canonical facts only. Unknown
    # values trigger review when the corresponding candidate constraint is saved.
    job_fee_present = "application_fee_amount" in job_metadata
    job_fee = job_metadata.get("application_fee_amount")
    job_fee_currency = str(job_metadata.get("application_fee_currency") or "").strip().upper()
    if route_classification == "PAID" and (
        not job_fee_present
        or isinstance(job_fee, bool)
        or not isinstance(job_fee, (int, float))
        or not math.isfinite(float(job_fee))
        or float(job_fee) <= 0
    ):
        review_flags.append(
            "The application route is classified as paid, but no valid positive fee amount is recorded."
        )
    if max_fee_configured and route_classification not in {"BLOCKED", "PAID_OR_BLOCKED"}:
        if not job_fee_present or isinstance(job_fee, bool) or not isinstance(job_fee, (int, float)):
            review_flags.append("The candidate's maximum application fee is recorded, but the job's application fee is unknown.")
        else:
            try:
                fee_amount = float(job_fee)
                fee_limit = float(max_application_fee)
                if fee_amount < 0 or fee_limit < 0:
                    raise ValueError("fee amounts cannot be negative")
                if fee_amount > 0 and fee_limit > 0:
                    if not job_fee_currency or not max_application_fee_currency:
                        review_flags.append("Application fee currencies are missing; the fee limit cannot be compared safely.")
                    elif job_fee_currency != max_application_fee_currency:
                        review_flags.append("Application fee currencies differ; the fee limit cannot be compared safely.")
                    elif fee_amount > fee_limit:
                        blockers.append({
                            "code": "APPLICATION_FEE_EXCEEDS_MAXIMUM",
                            "reason": f"Recorded application fee ({fee_amount} {job_fee_currency}) exceeds the candidate's maximum ({fee_limit} {max_application_fee_currency}).",
                        })
                elif fee_amount > fee_limit:
                    blockers.append({
                        "code": "APPLICATION_FEE_EXCEEDS_MAXIMUM",
                        "reason": f"Recorded application fee ({fee_amount}) exceeds the candidate's maximum ({fee_limit}).",
                    })
            except (TypeError, ValueError):
                review_flags.append("Application fee data is invalid; the fee limit cannot be compared safely.")

    job_relocation_required = job_metadata.get("relocation_required")
    if job_relocation_required is True and relocation_preference_configured:
        if willing_to_relocate is False:
            blockers.append({
                "code": "RELOCATION_NOT_ACCEPTED",
                "reason": "The job explicitly requires relocation, which the candidate has declined.",
            })
        elif willing_to_relocate is not True:
            review_flags.append("The job explicitly requires relocation, but the candidate's relocation preference is unknown.")
    elif job_relocation_required is not None and not isinstance(job_relocation_required, bool):
        review_flags.append("The job's relocation requirement is malformed and needs verification.")

    if blockers:
        return {
            "outcome": "REJECT", "score": 0, "hard_blockers": blockers,
            "evidence_summary": {"approved_evidence_count": len(candidate_evidence), "matched_terms": [], "missing_terms": []},
            "explanation": {"reasons": [item["reason"] for item in blockers], "gaps": [], "confidence": "HIGH"},
            "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
        }

    lifecycle = str(job.get("lifecycle_status") or "").upper()
    freshness = str(job.get("freshness_status") or "UNKNOWN").upper()
    if lifecycle in {"CLOSED", "EXPIRED", "ARCHIVED", "REMOVED"}:
        return {
            "outcome": "WATCH", "score": None, "hard_blockers": [],
            "evidence_summary": {
                "approved_evidence_count": len(candidate_evidence), "matched_terms": [], "missing_terms": [],
                "dimensions": {"job_freshness": freshness, "application_accessibility": "UNAVAILABLE"},
            },
            "explanation": {
                "reasons": ["The job is recorded as closed, expired, archived, or removed; do not apply unless its status is reverified."],
                "gaps": ["Verify whether the employer has reopened the role."], "confidence": "HIGH",
            },
            "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
        }

    if freshness == "STALE":
        return {
            "outcome": "WATCH", "score": None, "hard_blockers": [],
            "evidence_summary": {
                "approved_evidence_count": len(candidate_evidence), "matched_terms": [], "missing_terms": [],
                "dimensions": {"job_freshness": "STALE", "application_accessibility": "UNVERIFIED"},
            },
            "explanation": {
                "reasons": ["The job record is stale; matching quality cannot establish that the opportunity is still available."],
                "gaps": ["Refresh the source observation and recheck job lifecycle before considering a Punt."],
                "confidence": "HIGH",
            },
            "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
        }
    if route_classification in {"BLOCKED", "PAID_OR_BLOCKED"}:
        return {
            "outcome": "WATCH", "score": None, "hard_blockers": [],
            "evidence_summary": {
                "approved_evidence_count": len(candidate_evidence),
                "matched_terms": [], "missing_terms": [],
                "dimensions": {
                    "job_freshness": freshness,
                    "application_accessibility": "UNAVAILABLE" if route_classification == "BLOCKED" else "UNKNOWN",
                    "application_route_classification": route_classification,
                },
            },
            "explanation": {
                "reasons": [
                    "The application route is explicitly blocked."
                    if route_classification == "BLOCKED"
                    else "The source classifies the route as paid-or-blocked without enough detail to determine access safely."
                ],
                "gaps": ["Resolve application-route accessibility and cost before considering this opportunity a Punt."],
                "confidence": "HIGH" if route_classification == "BLOCKED" else "LOW",
            },
            "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
        }

    if salary_review_gap:
        return {
            "outcome": "REVIEW", "score": None, "hard_blockers": [],
            "evidence_summary": {
                "approved_evidence_count": len(candidate_evidence),
                "matched_terms": [], "missing_terms": [],
                "dimensions": {"salary_eligibility": "UNKNOWN"},
            },
            "explanation": {
                "reasons": ["Salary eligibility cannot be established from comparable, disclosed data."],
                "gaps": [salary_review_gap], "confidence": "LOW",
            },
            "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
        }

    if not has_substantive_job_description(jd) or (description_completeness or "").upper() in {"INCOMPLETE", "MISSING", "UNKNOWN", "PARTIAL", "UNAVAILABLE", "REQUIRES_REVIEW"}:
        return {
            "outcome": "REVIEW", "score": None, "hard_blockers": blockers,
            "evidence_summary": {"approved_evidence_count": len(candidate_evidence), "matched_terms": [], "missing_terms": []},
            "explanation": {"reasons": ["The job description is missing or too incomplete for a reliable evaluation."], "gaps": ["Obtain and verify a complete job description."], "confidence": "LOW"},
            "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
        }

    if review_flags:
        return {
            "outcome": "REVIEW", "score": None, "hard_blockers": [],
            "evidence_summary": {"approved_evidence_count": len(candidate_evidence), "matched_terms": [], "missing_terms": []},
            "explanation": {
                "reasons": ["A potentially decisive eligibility requirement cannot be verified from the recorded candidate data."],
                "gaps": review_flags, "confidence": "LOW",
            },
            "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
        }

    if lifecycle not in {"ACTIVE", "OPEN"}:
        return {
            "outcome": "REVIEW", "score": None, "hard_blockers": [],
            "evidence_summary": {
                "approved_evidence_count": len(candidate_evidence), "matched_terms": [], "missing_terms": [],
                "dimensions": {
                    "job_lifecycle": "UNKNOWN",
                    "job_freshness": freshness,
                    "application_accessibility": "UNVERIFIED",
                },
            },
            "explanation": {
                "reasons": ["The job lifecycle is not verified as active; a stored record is not proof that the role is open."],
                "gaps": ["Verify the current job lifecycle before making an application recommendation."],
                "confidence": "LOW",
            },
            "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
        }

    if freshness not in {"FRESH", "CURRENT"}:
        return {
            "outcome": "REVIEW", "score": None, "hard_blockers": [],
            "evidence_summary": {
                "approved_evidence_count": len(candidate_evidence), "matched_terms": [], "missing_terms": [],
                "dimensions": {"job_freshness": "UNKNOWN", "application_accessibility": "UNVERIFIED"},
            },
            "explanation": {
                "reasons": ["The job's freshness is unknown; availability must not be inferred from a stored record."],
                "gaps": ["Verify the current job status before making an application recommendation."],
                "confidence": "LOW",
            },
            "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
        }

    # An unresolved application route means the opportunity is not ready to pursue.
    # Do not infer accessibility from a strong evidence score.
    from urllib.parse import urlsplit

    application_url = str(job.get("application_url") or "").strip()
    try:
        route_parts = urlsplit(application_url)
        route_is_valid = route_parts.scheme.lower() in {"http", "https"} and bool(route_parts.netloc)
    except ValueError:
        route_is_valid = False
    if not route_is_valid:
        return {
            "outcome": "WATCH", "score": None, "hard_blockers": [],
            "evidence_summary": {
                "approved_evidence_count": len(candidate_evidence), "matched_terms": [], "missing_terms": [],
                "dimensions": {"job_freshness": freshness, "application_accessibility": "UNKNOWN"},
            },
            "explanation": {
                "reasons": ["The application route is missing or invalid; verify how the candidate can apply before pursuing the role."],
                "gaps": ["Verify a working application URL or an approved alternative route."], "confidence": "LOW",
            },
            "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
        }

    route_verification = job.get("application_route_verification") or {}
    if (
        str(route_verification.get("status") or "").upper() != "ACTIVE"
        or route_verification.get("checked_url") != application_url
    ):
        return {
            "outcome": "WATCH", "score": None, "hard_blockers": [],
            "evidence_summary": {
                "approved_evidence_count": len(candidate_evidence),
                "matched_terms": [], "missing_terms": [],
                "dimensions": {
                    "job_freshness": freshness,
                    "application_accessibility": "UNVERIFIED",
                },
            },
            "explanation": {
                "reasons": ["The application route has no current successful verification for the recorded URL."],
                "gaps": ["Run or refresh the application-route verifier before considering this opportunity a Punt."],
                "confidence": "LOW",
            },
            "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
        }

    # Semantic evidence matching must be supplied by the governed AI task. A
    # lexical heuristic is never allowed to qualify a candidate for a Punt.
    if not semantic_matches:
        return {
            "outcome": "REVIEW", "score": None, "hard_blockers": [],
            "evidence_summary": {
                "approved_evidence_count": len(candidate_evidence),
                "matched_terms": [], "missing_terms": [],
                "role_preference_match": _contains_any(title.casefold(), [str(x) for x in target_roles]) if target_roles else None,
                "dimensions": {
                    "job_freshness": freshness,
                    "application_accessibility": "VERIFIED_ACTIVE",
                    "semantic_matching": "UNAVAILABLE",
                },
            },
            "explanation": {
                "reasons": reasons + ["No validated semantic requirement-to-evidence assessment is available; keyword overlap is not sufficient to recommend applying."],
                "gaps": ["Run the governed semantic evidence-matching task and validate every requirement/evidence reference."],
                "confidence": "LOW",
            },
            "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
        }

    evidence_ids = {str(item.get("id")) for item in candidate_evidence if item.get("id")}
    validated_matches: list[dict[str, Any]] = []
    for raw_match in semantic_matches:
        if not isinstance(raw_match, dict):
            validated_matches = []
            break
        match = _canonical_match(raw_match)
        if match.get("status") not in _VALID_MATCH_STATES:
            validated_matches = []
            break
        refs = match.get("evidence_refs", [])
        if not isinstance(refs, list) or any(str(ref) not in evidence_ids for ref in refs):
            validated_matches = []
            break
        if match.get("status") in _SUPPORTED_MATCH_STATES and not refs:
            validated_matches = []
            break
        validated_matches.append(match)

    if not validated_matches:
        return {
            "outcome": "REVIEW", "score": None, "hard_blockers": [],
            "evidence_summary": {"approved_evidence_count": len(candidate_evidence), "requirement_matches": []},
            "explanation": {"reasons": ["No job requirements could be semantically assessed."], "gaps": ["Review the job description and extracted requirements."], "confidence": "LOW"},
            "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
        }

    supported = [m for m in validated_matches if m["status"] in _SUPPORTED_MATCH_STATES]
    mandatory = [m for m in validated_matches if m.get("mandatory") is True]
    mandatory_unmet = [m for m in mandatory if m["status"] not in _SUPPORTED_MATCH_STATES]
    score = round(100 * len(supported) / len(validated_matches)) if candidate_evidence else None
    if candidate_evidence:
        reasons.append(f"Semantic assessment found supported evidence for {len(supported)} of {len(validated_matches)} assessed requirements.")
    else:
        reasons.append("No approved candidate evidence is available; absence of evidence is not proof that the candidate lacks the capability.")
    gaps.extend(
        f"{m.get('requirement_text', 'Requirement')} is {m['status']} based on the supplied approved evidence."
        for m in validated_matches if m["status"] not in _SUPPORTED_MATCH_STATES
    )
    if not candidate_evidence:
        gaps.append("No approved candidate evidence is available; draft or unverified evidence was not used.")

    if mandatory_unmet:
        outcome = "REVIEW"
        if candidate_evidence:
            reasons.append(f"{len(mandatory_unmet)} mandatory requirement(s) are not supported by direct or transferable evidence.")
        else:
            reasons.append("Mandatory requirements cannot be verified until approved candidate evidence is available.")
        confidence = "LOW" if not candidate_evidence or any(
            m["status"] == "REQUIRES_VERIFICATION" for m in mandatory_unmet
        ) else "MEDIUM"
    elif not candidate_evidence:
        outcome = "REVIEW"
        reasons.append("No approved candidate evidence is available; absence of evidence is not proof that the candidate lacks the capability.")
        confidence = "LOW"
    elif score is not None and score >= 70:
        outcome = "APPLY"
        reasons.append("Validated semantic matching supports the mandatory requirements and the overall evidence threshold.")
        confidence = "MEDIUM"
    elif score is not None and score >= 35:
        outcome = "REVIEW"
        reasons.append("Semantic evidence is mixed and needs candidate review.")
        confidence = "LOW" if not candidate_evidence else "MEDIUM"
    else:
        outcome = "WATCH"
        reasons.append("Current approved evidence does not establish sufficient role fit.")
        confidence = "LOW" if not candidate_evidence else "MEDIUM"
    match_state_counts = {
        state: sum(1 for match in validated_matches if match["status"] == state)
        for state in sorted(_VALID_MATCH_STATES)
    }
    return {
        "outcome": outcome, "score": score, "hard_blockers": [],
        "evidence_summary": {
            "approved_evidence_count": len(candidate_evidence),
            "requirement_matches": validated_matches,
            "requirement_match_state_counts": match_state_counts,
            "supported_requirement_count": len(supported),
            "unmet_mandatory_requirement_count": len(mandatory_unmet),
            "dimensions": {
                "deterministic_eligibility": "ELIGIBLE",
                "job_freshness": freshness,
                "application_accessibility": "VERIFIED_ACTIVE",
                "requirement_evidence_coverage": score,
                "mandatory_requirements": (
                    "MET" if not mandatory_unmet
                    else "REQUIRES_REVIEW" if not candidate_evidence
                    else "UNMET"
                ),
            },
            "role_preference_match": _contains_any(title.casefold(), [str(x) for x in target_roles]) if target_roles else None,
            "industry_preference_match": _contains_any(job_industry.casefold(), [str(x) for x in target_industries]) if job_industry and target_industries else None,
        },
        "explanation": {"reasons": reasons, "gaps": gaps, "confidence": confidence},
        "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
    }


_DIMENSION_KEYS = (
    "core_role_fit",
    "domain_fit",
    "technical_fit",
    "seniority_fit",
    "education_fit",
)


def _assessment_for_matches(matches: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = [str(match.get("status") or "MISSING").upper() for match in matches]
    supported = sum(status in _SUPPORTED_MATCH_STATES for status in statuses)
    if "DISQUALIFYING" in statuses:
        assessment = "DISQUALIFYING"
    elif "CONTRADICTED" in statuses:
        assessment = "CONTRADICTED"
    elif "REQUIRES_VERIFICATION" in statuses:
        assessment = "REQUIRES_VERIFICATION"
    elif statuses and supported == len(statuses):
        assessment = "MET"
    elif supported:
        assessment = "PARTIAL"
    elif statuses:
        assessment = "UNMET"
    else:
        assessment = "UNKNOWN"
    refs = sorted({
        str(ref)
        for match in matches
        for ref in (match.get("evidence_refs") or [])
    })
    return {
        "status": assessment,
        "score": round(100 * supported / len(statuses)) if statuses else None,
        "requirement_count": len(matches),
        "evidence_refs": refs,
        "gaps": [
            str(match.get("requirement_text") or "Requirement")
            for match in matches
            if str(match.get("status") or "").upper() not in _SUPPORTED_MATCH_STATES
        ],
    }


def evaluate_candidate_job(
    *,
    job: dict[str, Any],
    job_description: str,
    description_version_id: str | None,
    description_completeness: str | None,
    candidate_evidence: list[dict[str, Any]],
    preferences: dict[str, Any],
    semantic_matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate deterministically and expose the PRD's separate fit/eligibility dimensions."""
    result = _evaluate_candidate_job_impl(
        job=job,
        job_description=job_description,
        description_version_id=description_version_id,
        description_completeness=description_completeness,
        candidate_evidence=candidate_evidence,
        preferences=preferences,
        semantic_matches=semantic_matches,
    )
    summary = result.setdefault("evidence_summary", {})
    existing_dimensions = dict(summary.get("dimensions") or {})
    route_classification = _route_classification(job)
    existing_dimensions["application_route_classification"] = route_classification
    summary["dimensions"] = existing_dimensions
    matches = summary.get("requirement_matches") or []
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in _DIMENSION_KEYS}
    for match in matches:
        dimension = match.get("dimension")
        if dimension in grouped:
            grouped[dimension].append(match)

    assessments = {
        key: _assessment_for_matches(grouped[key])
        for key in _DIMENSION_KEYS
    }
    if not candidate_evidence:
        for assessment in assessments.values():
            if assessment["requirement_count"] and assessment["status"] not in {
                "CONTRADICTED", "DISQUALIFYING",
            }:
                assessment["status"] = "NO_EVIDENCE"
                assessment["score"] = None
    blockers = result.get("hard_blockers") or []
    blocker_codes = {str(item.get("code") or "") for item in blockers if isinstance(item, dict)}
    location_blockers = {
        "COUNTRY_INELIGIBLE", "JOB_LOCATION_RESTRICTION", "REMOTE_ONLY_MISMATCH",
        "RELOCATION_NOT_ACCEPTED",
    }
    preference_blockers = {
        "EXCLUDED_INDUSTRY", "EXCLUDED_JOB_TYPE", "EMPLOYMENT_TYPE_MISMATCH",
        "APPLICATION_FEE_EXCEEDS_MAXIMUM", "SALARY_BELOW_MINIMUM",
    }
    explanation = result.get("explanation") or {}
    gaps = [str(item) for item in explanation.get("gaps", [])]
    reasons = [str(item) for item in explanation.get("reasons", [])]
    restrictions = job.get("location_restrictions") or {}
    location_relevant = bool(
        preferences.get("eligibleCountries") or preferences.get("eligible_countries")
        or preferences.get("workAuthorizationCountries") or preferences.get("work_authorization_countries")
        or preferences.get("preferredLocations") or preferences.get("preferred_locations")
        or str(preferences.get("remotePreference") or preferences.get("remote_preference") or "").upper() == "REMOTE_ONLY"
        or restrictions.get("countries") or restrictions.get("eligibleCountries")
        or re.search(
            r"work authorization|required authorization|must be authorized|citizen(ship)? required|visa sponsorship (?:is )?not available",
            job_description or "", re.I,
        )
    )
    if blocker_codes & location_blockers:
        location_status = "DISQUALIFYING"
    elif not location_relevant:
        location_status = "NOT_REQUIRED"
    elif result.get("outcome") == "APPLY":
        location_status = "MET"
    elif any(
        term in item.casefold()
        for item in gaps + reasons
        for term in ("location", "authorization", "country", "remote", "relocation")
    ):
        location_status = "REQUIRES_REVIEW"
    else:
        location_status = "UNKNOWN"
    if blocker_codes & preference_blockers:
        preference_status = "DISQUALIFYING"
    elif result.get("outcome") == "APPLY" and summary.get("role_preference_match") is False:
        preference_status = "PARTIAL"
    elif result.get("outcome") == "APPLY":
        preference_status = "MET"
    elif any(
        term in item.casefold()
        for item in gaps + reasons
        for term in ("preference", "salary", "relocation", "application fee", "employment type", "industry")
    ):
        preference_status = "REQUIRES_REVIEW"
    else:
        preference_status = "UNKNOWN"

    lifecycle = str(job.get("lifecycle_status") or "UNKNOWN").upper()
    freshness = str(job.get("freshness_status") or "UNKNOWN").upper()
    if freshness == "STALE":
        freshness_status = "STALE"
    elif freshness in {"FRESH", "CURRENT"}:
        freshness_status = "CURRENT"
    else:
        freshness_status = "UNKNOWN"

    accessibility = str(existing_dimensions.get("application_accessibility") or "").upper()
    if not accessibility:
        url = str(job.get("application_url") or "").strip()
        from urllib.parse import urlsplit
        try:
            parsed_url = urlsplit(url)
            accessibility = "VERIFIED" if parsed_url.scheme.lower() in {"http", "https"} and parsed_url.netloc else "UNKNOWN"
        except ValueError:
            accessibility = "UNKNOWN"
    elif accessibility in {"VERIFIED_URL", "VERIFIED_ACTIVE"}:
        accessibility = "VERIFIED"
    elif accessibility == "UNAVAILABLE":
        accessibility = "UNAVAILABLE"

    assessments["location_work_authorization"] = {
        "status": location_status,
        "score": None,
        "requirement_count": 0,
        "evidence_refs": [],
        "gaps": [gap for gap in gaps if "location" in gap.casefold() or "authorization" in gap.casefold()],
    }
    assessments["preference_fit"] = {
        "status": preference_status,
        "score": None,
        "requirement_count": 0,
        "evidence_refs": [],
        "gaps": [gap for gap in gaps if "preference" in gap.casefold() or "salary" in gap.casefold() or "relocation" in gap.casefold()],
    }
    assessments["application_accessibility"] = {
        "status": accessibility,
        "classification": route_classification,
        "score": None,
        "requirement_count": 0,
        "evidence_refs": [],
        "gaps": [gap for gap in gaps if "application route" in gap.casefold() or "application url" in gap.casefold()],
    }
    lifecycle_assessment = (
        "CLOSED" if lifecycle in {"CLOSED", "EXPIRED", "ARCHIVED", "REMOVED"}
        else "ACTIVE" if lifecycle in {"ACTIVE", "OPEN"}
        else "UNKNOWN"
    )
    existing_dimensions["job_lifecycle"] = lifecycle_assessment
    existing_dimensions["job_freshness"] = freshness_status
    summary["dimensions"] = existing_dimensions
    assessments["job_lifecycle"] = {
        "status": lifecycle_assessment,
        "score": None,
        "requirement_count": 0,
        "evidence_refs": [],
        "gaps": [gap for gap in gaps if "lifecycle" in gap.casefold() or "open" in gap.casefold()],
    }
    assessments["job_freshness"] = {
        "status": freshness_status,
        "score": None,
        "requirement_count": 0,
        "evidence_refs": [],
        "gaps": [gap for gap in gaps if "fresh" in gap.casefold() or "availability" in gap.casefold() or "status" in gap.casefold()],
    }
    summary["dimension_assessments"] = assessments
    return result
