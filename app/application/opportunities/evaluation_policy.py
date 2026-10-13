"""Deterministic, explainable candidate-to-job evaluation policy.

This policy intentionally does not call an LLM. Hard constraints take precedence
over evidence scoring; missing evidence is uncertainty, not proof of inability.
"""
from __future__ import annotations

import re
from typing import Any
from app.domain.jobs.completeness import has_substantive_job_description

POLICY_VERSION = "candidate-evaluation-v2"


def _normal(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return " ".join(_normal(item) for item in (value.values() if isinstance(value, dict) else value))
    return str(value).casefold()


def _contains_any(text: str, values: list[str]) -> bool:
    return any(value.strip().casefold() in text for value in values if value and value.strip())


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
    """Return an explainable evaluation payload without persisting or mutating inputs."""
    blockers: list[dict[str, str]] = []
    review_flags: list[str] = []
    reasons: list[str] = []
    gaps: list[str] = []
    jd = (job_description or "").strip()
    job_text = " ".join((str(job.get("canonical_title") or ""), str(job.get("company_name") or ""), jd)).casefold()
    evidence_text = " ".join(_normal(item.get("content")) for item in candidate_evidence).casefold()
    title = str(job.get("canonical_title") or "")
    target_roles = preferences.get("targetRoles") or preferences.get("target_roles") or []
    excluded_industries = preferences.get("excludedIndustries") or preferences.get("excluded_industries") or []
    target_industries = preferences.get("targetIndustries") or preferences.get("target_industries") or []
    eligible_countries = preferences.get("eligibleCountries") or preferences.get("eligible_countries") or []
    authorization_countries = preferences.get("workAuthorizationCountries") or preferences.get("work_authorization_countries") or []
    preferred_locations = preferences.get("preferredLocations") or preferences.get("preferred_locations") or []
    excluded_job_types = preferences.get("excludedJobTypes") or preferences.get("excluded_job_types") or []
    employment_types = preferences.get("employmentTypes") or preferences.get("employment_types") or []
    remote_preference = preferences.get("remotePreference") or preferences.get("remote_preference") or "FLEXIBLE"
    salary_min = preferences.get("salaryMin", preferences.get("salary_min"))
    salary_currency = str(preferences.get("salaryCurrency") or preferences.get("salary_currency") or "").strip().upper()
    salary_period = str(preferences.get("salaryPeriod") or preferences.get("salary_period") or "UNSPECIFIED").strip().upper()

    job_industry = str((job.get("metadata") or {}).get("industry") or "")
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

    if blockers:
        return {
            "outcome": "REJECT", "score": 0, "hard_blockers": blockers,
            "evidence_summary": {"approved_evidence_count": len(candidate_evidence), "matched_terms": [], "missing_terms": []},
            "explanation": {"reasons": [item["reason"] for item in blockers], "gaps": [], "confidence": "HIGH"},
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
                    "application_accessibility": "VERIFIED_URL",
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
    valid_statuses = {"direct", "transferable", "partial", "missing", "contradicted", "unverified"}
    validated_matches: list[dict[str, Any]] = []
    for match in semantic_matches:
        if not isinstance(match, dict) or match.get("status") not in valid_statuses:
            validated_matches = []
            break
        refs = match.get("evidence_refs", [])
        if not isinstance(refs, list) or any(str(ref) not in evidence_ids for ref in refs):
            validated_matches = []
            break
        if match.get("status") in {"direct", "transferable"} and not refs:
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

    supported = [m for m in validated_matches if m["status"] in {"direct", "transferable"}]
    mandatory = [m for m in validated_matches if m.get("mandatory") is True]
    mandatory_unmet = [m for m in mandatory if m["status"] not in {"direct", "transferable"}]
    score = round(100 * len(supported) / len(validated_matches))
    reasons.append(f"Semantic assessment found supported evidence for {len(supported)} of {len(validated_matches)} assessed requirements.")
    gaps.extend(
        f"{m.get('requirement_text', 'Requirement')} is {m['status']} based on the supplied approved evidence."
        for m in validated_matches if m["status"] not in {"direct", "transferable"}
    )
    if not candidate_evidence:
        gaps.append("No approved candidate evidence is available; draft or unverified evidence was not used.")

    if mandatory_unmet:
        outcome = "REVIEW"
        reasons.append(f"{len(mandatory_unmet)} mandatory requirement(s) are not supported by direct or transferable evidence.")
        confidence = "LOW" if any(m["status"] == "unverified" for m in mandatory_unmet) else "MEDIUM"
    elif score >= 70 and candidate_evidence:
        outcome = "APPLY"
        reasons.append("Validated semantic matching supports the mandatory requirements and the overall evidence threshold.")
        confidence = "MEDIUM"
    elif score >= 35:
        outcome = "REVIEW"
        reasons.append("Semantic evidence is mixed and needs candidate review.")
        confidence = "LOW" if not candidate_evidence else "MEDIUM"
    else:
        outcome = "WATCH"
        reasons.append("Current approved evidence does not establish sufficient role fit.")
        confidence = "LOW" if not candidate_evidence else "MEDIUM"
    return {
        "outcome": outcome, "score": score, "hard_blockers": [],
        "evidence_summary": {
            "approved_evidence_count": len(candidate_evidence),
            "requirement_matches": validated_matches,
            "supported_requirement_count": len(supported),
            "unmet_mandatory_requirement_count": len(mandatory_unmet),
            "role_preference_match": _contains_any(title.casefold(), [str(x) for x in target_roles]) if target_roles else None,
            "industry_preference_match": _contains_any(job_industry.casefold(), [str(x) for x in target_industries]) if job_industry and target_industries else None,
        },
        "explanation": {"reasons": reasons, "gaps": gaps, "confidence": confidence},
        "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
    }
