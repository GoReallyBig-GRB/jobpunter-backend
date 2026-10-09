"""Deterministic, explainable candidate-to-job evaluation policy.

This policy intentionally does not call an LLM. Hard constraints take precedence
over evidence scoring; missing evidence is uncertainty, not proof of inability.
"""
from __future__ import annotations

import re
from typing import Any

POLICY_VERSION = "candidate-evaluation-v1"


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

    job_industry = str((job.get("metadata") or {}).get("industry") or "")
    job_type = str(job.get("employment_type") or "")
    country = str(job.get("location_country") or "")
    work_mode = str(job.get("work_mode") or "").upper()
    restrictions = job.get("location_restrictions") or {}

    if job_industry and _contains_any(job_industry.casefold(), [str(x) for x in excluded_industries]):
        blockers.append({"code": "EXCLUDED_INDUSTRY", "reason": f"Job industry '{job_industry}' is explicitly excluded in preferences."})
    if job_type and _contains_any(job_type.casefold(), [str(x) for x in excluded_job_types]):
        blockers.append({"code": "EXCLUDED_JOB_TYPE", "reason": f"Employment type '{job_type}' is explicitly excluded in preferences."})
    if country and eligible_countries and not _contains_any(country.casefold(), [str(x) for x in eligible_countries]):
        blockers.append({"code": "COUNTRY_INELIGIBLE", "reason": f"Job country '{country}' is outside the candidate's recorded eligible-country preferences."})
    restricted_countries = restrictions.get("countries") or restrictions.get("eligibleCountries") or []
    if restricted_countries and country and not _contains_any(country.casefold(), [str(x) for x in restricted_countries]):
        blockers.append({"code": "JOB_LOCATION_RESTRICTION", "reason": f"Job location '{country}' conflicts with the job's recorded country restrictions."})
    if re.search(r"work authorization|required authorization|must be authorized|citizen(ship)? required|visa sponsorship (?:is )?not available", jd, re.I):
        if not authorization_countries:
            review_flags.append("The job mentions a work-authorization constraint, but candidate authorization is not recorded.")
        elif country and not _contains_any(country.casefold(), [str(x) for x in authorization_countries]):
            review_flags.append("The job's work-authorization requirement needs verification against the candidate's recorded authorization.")
    if remote_preference == "REMOTE_ONLY" and work_mode and work_mode not in {"REMOTE", "FULLY_REMOTE"}:
        blockers.append({"code": "REMOTE_ONLY_MISMATCH", "reason": f"Remote-only preference conflicts with recorded work mode '{work_mode}'."})

    if blockers:
        return {
            "outcome": "REJECT", "score": 0, "hard_blockers": blockers,
            "evidence_summary": {"approved_evidence_count": len(candidate_evidence), "matched_terms": [], "missing_terms": []},
            "explanation": {"reasons": [item["reason"] for item in blockers], "gaps": [], "confidence": "HIGH"},
            "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
        }

    if not jd or len(jd) < 80 or (description_completeness or "").upper() in {"INCOMPLETE", "MISSING", "UNKNOWN", "PARTIAL"}:
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

    # Only a transparent lexical signal; never interpreted as proof of qualification.
    words = [w for w in re.findall(r"[a-z][a-z0-9+#.-]{2,}", jd.casefold()) if w not in {
        "with", "that", "this", "from", "will", "your", "have", "they", "their",
        "years", "experience", "required", "requirements", "ability", "strong",
        "must", "role", "team", "work", "about", "what", "which", "also",
    }]
    unique_words = list(dict.fromkeys(words))
    matched = [word for word in unique_words if word in evidence_text]
    missing = [word for word in unique_words if word not in evidence_text]
    signal = len(matched) / max(1, len(unique_words))
    role_match = _contains_any(title.casefold(), [str(x) for x in target_roles]) if target_roles else False
    industry_match = _contains_any(job_industry.casefold(), [str(x) for x in target_industries]) if job_industry and target_industries else False
    preference_adjustment = 0
    if target_roles:
        preference_adjustment += 15 if role_match else -5
        reasons.append("Role title aligns with a saved target role." if role_match else "Role title does not directly match a saved target role.")
    if target_industries and job_industry:
        preference_adjustment += 5 if industry_match else -5
    if employment_types:
        preference_adjustment += 5 if _contains_any(job_type.casefold(), [str(x) for x in employment_types]) else 0
    score = round(max(0, min(100, signal * 80 + preference_adjustment)))
    if matched:
        reasons.append(f"Approved candidate evidence overlaps with {len(matched)} distinct job-description terms.")
    if missing:
        gaps.append(f"Evidence does not currently document {len(missing)} of the evaluated description terms.")
    if not candidate_evidence:
        gaps.append("No approved candidate evidence is available; draft or unverified evidence was not used.")
    if score >= 70 and candidate_evidence:
        outcome = "APPLY"
        reasons.append("The deterministic fit signal is strong enough to recommend considering an application.")
        confidence = "MEDIUM"
    elif score >= 30:
        outcome = "REVIEW"
        reasons.append("The fit signal is mixed and needs human review.")
        confidence = "LOW" if not candidate_evidence else "MEDIUM"
    else:
        outcome = "WATCH"
        reasons.append("Current evidence does not establish a strong fit; review again if new evidence or job details become available.")
        confidence = "LOW" if not candidate_evidence else "MEDIUM"

    return {
        "outcome": outcome, "score": score, "hard_blockers": [],
        "evidence_summary": {
            "approved_evidence_count": len(candidate_evidence),
            "matched_terms": matched[:30], "missing_terms": missing[:30],
            "role_preference_match": role_match if target_roles else None,
            "industry_preference_match": industry_match if job_industry and target_industries else None,
        },
        "explanation": {"reasons": reasons, "gaps": gaps, "confidence": confidence},
        "policy_version": POLICY_VERSION, "job_description_version_id": description_version_id,
    }
