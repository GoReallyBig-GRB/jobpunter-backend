"""Profile-driven deterministic requirement extraction and evidence matching.

This deliberately avoids the legacy fallback's hardcoded candidate/project assumptions.
It is a helper, not the complete persisted job-evaluation pipeline.
"""
from __future__ import annotations

import re
from typing import Any


_REQUIREMENT_TRIGGER = re.compile(
    r"must have|required|requirements|qualifications|experience in|proficient|responsible for",
    re.IGNORECASE,
)
_EXPLICIT_SENIORITY = re.compile(
    r"(?:\d+\+?\s*years?|senior product manager|director|vice president|vp of|c-level|executive|managing \d+)",
    re.IGNORECASE,
)
_EXPLICIT_MUST_HAVE = re.compile(
    r"must have|mandatory|required:|requirements:|strict requirement|minimum \d+|"
    r"candidates without .* will not be considered|must be|(?:sales|revenue|monthly|annual)\s*quota|quota-carrying|commission",
    re.IGNORECASE,
)
_STOP_WORDS = {
    "with", "from", "have", "experience", "must", "responsible", "required",
    "work", "ability", "strong", "that", "this", "will", "your", "their",
    "the", "and", "for", "into", "over", "using", "use", "such",
}


def _items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if item is not None and str(item).strip()]


def _profile_text(profile: dict[str, Any]) -> str:
    return " ".join(
        str(profile.get(key) or "")
        for key in ("rawText", "corePositioning", "workingMethod", "primaryProfession")
    ).casefold()


def extract_requirements_deterministically(
    jd_text: str, job_title: str | None = None
) -> list[dict[str, Any]]:
    """Port the legacy deterministic JD fallback without changing its limits/categories."""
    lines = [line.strip() for line in (jd_text or "").split("\n") if len(line.strip()) > 15]
    results: list[dict[str, Any]] = []
    counter = 1

    if job_title and job_title.strip():
        results.append({
            "id": f"req_det_{counter}",
            "requirement": f"Core role focus: {job_title.strip()}",
            "category": "ROLE_CORE",
            "severity": "IMPORTANT",
            "confidence": "HIGH",
        })
        counter += 1

    for line in lines:
        if len(results) >= 8:
            break
        lower = line.casefold()
        if not (_REQUIREMENT_TRIGGER.search(lower) or line.startswith(("•", "*", "-"))):
            continue
        clean = re.sub(r"^[•*-]\s*", "", line)
        clean = re.sub(r"^(?:must have|requirements?:?|required:?)\s*", "", clean, flags=re.IGNORECASE).strip()
        if not 15 < len(clean) < 180:
            continue

        seniority = bool(_EXPLICIT_SENIORITY.search(clean))
        explicit = bool(_EXPLICIT_MUST_HAVE.search(line))
        if seniority:
            category = "SENIORITY"
            severity = "MUST_HAVE" if explicit or re.search(r"(?:10\+|12\+|15\+|minimum \d+)", clean, re.I) else "IMPORTANT"
        elif re.search(r"degree|bachelor|master|certification|license|certified", clean, re.I):
            category, severity = "EDUCATION", "MUST_HAVE" if explicit else "IMPORTANT"
        elif re.search(r"remote|location|authorization|citizen|visa|hybrid", clean, re.I):
            category = "LOCATION"
            severity = "MUST_HAVE" if explicit or re.search(r"citizen|authorization|must be located", clean, re.I) else "IMPORTANT"
        elif re.search(r"api|sql|python|typescript|react|code|database|tool|webhook|microservices|state machine", clean, re.I):
            category, severity = "TECHNICAL", "MUST_HAVE" if explicit else "IMPORTANT"
        elif re.search(r"healthcare|fintech|b2b|saas|marketplace|ecommerce|hospital|clinical|lending|underwriting|banking", clean, re.I):
            category, severity = "DOMAIN", "MUST_HAVE" if explicit else "IMPORTANT"
        else:
            category, severity = "ROLE_CORE", "MUST_HAVE" if explicit else "IMPORTANT"

        results.append({
            "id": f"req_det_{counter}",
            "requirement": clean[:120],
            "category": category,
            "severity": severity,
            "confidence": "MEDIUM",
        })
        counter += 1

    if not results:
        results.append({
            "id": f"req_det_{counter}",
            "requirement": f"Core duties for {job_title or 'Role'} as specified in job description",
            "category": "ROLE_CORE",
            "severity": "IMPORTANT",
            "confidence": "LOW",
        })
    return results


def _evidence_items(profile: dict[str, Any]) -> list[dict[str, str]]:
    structured = profile.get("structuredData")
    if not isinstance(structured, dict):
        structured = {}
    items: list[dict[str, str]] = []

    for project in structured.get("projectsOrProducts", []) or []:
        if not isinstance(project, dict):
            continue
        name = str(project.get("name") or "Documented project")
        text_parts = [name, str(project.get("headline") or ""), str(project.get("context") or "")]
        for key in ("bullets", "capabilities", "keywords"):
            text_parts.extend(_items(project.get(key)))
        items.append({"name": name, "text": " ".join(text_parts).casefold()})

    for employment in structured.get("employmentHistory", []) or []:
        if not isinstance(employment, dict):
            continue
        role = str(employment.get("role") or "Documented role")
        organization = str(employment.get("organization") or "Employer not specified")
        text_parts = [role, organization]
        for key in ("responsibilities", "achievements", "skills"):
            text_parts.extend(_items(employment.get(key)))
        items.append({"name": f"{role} at {organization}", "text": " ".join(text_parts).casefold()})

    for skill in structured.get("skills", []) or []:
        if not isinstance(skill, dict):
            continue
        category = str(skill.get("category") or "Skill")
        items.append({
            "name": f"{category} (documented competency)",
            "text": " ".join([category, *_items(skill.get("items"))]).casefold(),
        })

    leadership = _items(structured.get("leadershipOwnership"))
    if leadership:
        items.append({"name": "Documented leadership and scope", "text": " ".join(leadership).casefold()})
    working_method = str(profile.get("workingMethod") or "").strip()
    if working_method:
        items.append({"name": "Documented working method", "text": working_method.casefold()})
    return items


def map_requirements_to_evidence(
    requirements: list[dict[str, Any]], profile: dict[str, Any]
) -> list[dict[str, Any]]:
    """Match requirements only to the supplied candidate's evidence and explicit preferences."""
    profile_text = _profile_text(profile)
    evidence_items = _evidence_items(profile)
    structured = profile.get("structuredData")
    if not isinstance(structured, dict):
        structured = {}
    preferences = profile.get("preferences")
    if not isinstance(preferences, dict):
        preferences = {}
    excluded_terms = [
        term.casefold()
        for key in ("excludedJobTypes", "excludedIndustries")
        for term in _items(preferences.get(key))
    ]
    profession = str(profile.get("primaryProfession") or "Candidate profile")
    results: list[dict[str, Any]] = []

    for requirement in requirements:
        text = str(requirement.get("requirement") or "")
        lowered = text.casefold()
        severity = str(requirement.get("severity") or "IMPORTANT")
        req_words = [
            word for word in re.findall(r"[\w+#.-]+", lowered)
            if len(word) > 3 and word not in _STOP_WORDS
        ]

        excluded = next((term for term in excluded_terms if term and term in lowered), None)
        if excluded:
            results.append({
                "id": requirement.get("id"),
                "requirement": text,
                "severity": "MUST_HAVE",
                "status": "DISQUALIFYING",
                "candidateEvidence": f"Explicit candidate exclusion: {excluded}",
                "sourceProduct": "Candidate preferences",
                "rationale": "Requirement conflicts with a persisted candidate exclusion.",
                "isMaterialGap": True,
                "provenance": {
                    "candidateEvidenceSource": "Candidate profile > Preferences",
                    "jdRequirementSource": "Job Posting",
                },
            })
            continue

        best_name = profession
        best_text = ""
        best_count = 0
        for item in evidence_items:
            count = sum(1 for word in req_words if word in item["text"])
            if count > best_count:
                best_name, best_text, best_count = item["name"], item["text"], count
        general_count = sum(1 for word in req_words if word in profile_text)
        ratio = max(best_count, general_count) / len(req_words) if req_words else 0.0

        if ratio >= 0.4:
            status = "DIRECT"
            candidate_evidence = f"Documented experience in {best_name}: matching terms were found in this candidate's supplied profile."
            rationale = "Deterministic text overlap suggests a direct match; verify source evidence before treating it as proven."
            material_gap = False
        elif ratio >= 0.2:
            status = "TRANSFERABLE"
            candidate_evidence = f"Related text appears in {best_name}; relevance needs human verification."
            rationale = "Partial lexical overlap suggests a possible transferable capability, not proof of competence."
            material_gap = False
        else:
            status = "MISSING"
            candidate_evidence = "No matching documented evidence was found in this candidate's supplied profile."
            rationale = "The deterministic matcher found insufficient textual overlap; this is not proof the candidate lacks the skill."
            material_gap = severity == "MUST_HAVE"

        results.append({
            "id": requirement.get("id"),
            "requirement": text,
            "severity": severity,
            "status": status,
            "candidateEvidence": candidate_evidence,
            "sourceProduct": best_name,
            "rationale": rationale,
            "isMaterialGap": material_gap,
            "provenance": {
                "candidateEvidenceSource": f"Candidate profile > {best_name}",
                "jdRequirementSource": "Job Posting",
            },
        })
    return results
