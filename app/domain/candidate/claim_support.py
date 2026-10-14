"""Conservative deterministic checks that candidate claim facts match evidence."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


_KEY_ALIASES = {
    "company": "employer",
    "company_name": "employer",
    "organization": "employer",
    "organization_name": "employer",
    "employer_name": "employer",
    "employer": "employer",
    "position": "role",
    "job_title": "role",
    "position_title": "role",
    "title": "role",
    "role": "role",
    "duration_years": "years",
    "years_experience": "years",
    "years": "years",
    "employment_start": "start_date",
    "startdate": "start_date",
    "start_date": "start_date",
    "employment_end": "end_date",
    "enddate": "end_date",
    "end_date": "end_date",
    "school": "institution",
    "school_name": "institution",
    "university": "institution",
    "university_name": "institution",
    "institution": "institution",
    "degree": "qualification",
    "degree_name": "qualification",
    "qualification_name": "qualification",
    "qualification": "qualification",
    "employment_start_date": "start_date",
    "employment_end_date": "end_date",
    "certification_name": "certification",
    "certificate": "certification",
    "certification": "certification",
    "issuing_body": "issuing_organization",
    "issuer": "issuing_organization",
    "issuing_organization": "issuing_organization",
    "project_name": "project_name",
}


def _canonical_key(value: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return _KEY_ALIASES.get(key, key)


def _normalized_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return " ".join(str(value).casefold().split())


def _flatten(value: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], str]]:
    facts: list[tuple[tuple[str, ...], str]] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                continue
            facts.extend(_flatten(nested, (*path, _canonical_key(key))))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            facts.extend(_flatten(nested, (*path, str(index))))
    elif value is not None:
        normalized = _normalized_value(value)
        if normalized:
            facts.append((path, normalized))
    return facts


def claim_facts_are_supported(
    claim_content: Mapping[str, Any],
    evidence_contents: Sequence[Mapping[str, Any]],
) -> bool:
    """Return true only when every asserted scalar claim fact appears in linked evidence.

    This is intentionally exact and conservative. It does not infer synonyms or assess
    whether evidence is authentic; those require separate review and provenance controls.
    """
    claim_facts = _flatten(claim_content)
    if not claim_facts:
        return False
    evidence_facts = {
        fact for evidence in evidence_contents for fact in _flatten(evidence)
    }
    return all(fact in evidence_facts for fact in claim_facts)



_RECORD_ANCHORS = {
    "employer",
    "role",
    "institution",
    "qualification",
    "project_name",
    "certification",
    "issuing_organization",
}


def _flatten_with_context(
    value: Any,
    path: tuple[str, ...] = (),
    context: Mapping[str, str] | None = None,
) -> list[tuple[tuple[str, ...], str, dict[str, str]]]:
    """Flatten facts while retaining stable record identifiers for cross-shape comparison."""
    inherited = dict(context or {})
    facts: list[tuple[tuple[str, ...], str, dict[str, str]]] = []
    if isinstance(value, Mapping):
        current = dict(inherited)
        for key, nested in value.items():
            if isinstance(key, str) and not isinstance(nested, (Mapping, list, tuple)):
                canonical = _canonical_key(key)
                if canonical in _RECORD_ANCHORS and nested is not None:
                    normalized = _normalized_value(nested)
                    if normalized:
                        current[canonical] = normalized
        for key, nested in value.items():
            if not isinstance(key, str):
                continue
            canonical = _canonical_key(key)
            facts.extend(_flatten_with_context(nested, (*path, canonical), current))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            facts.extend(_flatten_with_context(nested, (*path, str(index)), inherited))
    elif value is not None:
        normalized = _normalized_value(value)
        if normalized:
            facts.append((path, normalized, inherited))
    return facts


def _same_record_context(left: Mapping[str, str], right: Mapping[str, str]) -> bool:
    # A role alone is not enough to identify a specific employment record.
    if "employer" in left or "employer" in right:
        return (
            "employer" in left
            and "employer" in right
            and left["employer"] == right["employer"]
            and (
                "role" not in left
                or "role" not in right
                or left["role"] == right["role"]
            )
        )
    if "institution" in left or "institution" in right:
        return (
            "institution" in left
            and "institution" in right
            and left["institution"] == right["institution"]
            and (
                "qualification" not in left
                or "qualification" not in right
                or left["qualification"] == right["qualification"]
            )
        )
    if "project_name" in left or "project_name" in right:
        return (
            "project_name" in left
            and "project_name" in right
            and left["project_name"] == right["project_name"]
        )
    if "certification" in left or "certification" in right:
        return (
            "certification" in left
            and "certification" in right
            and left["certification"] == right["certification"]
            and (
                "issuing_organization" not in left
                or "issuing_organization" not in right
                or left["issuing_organization"] == right["issuing_organization"]
            )
        )
    return False


def claim_fact_conflicts_with_evidence(
    claim_content: Mapping[str, Any],
    evidence_contents: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Find contradictions across equivalent fields in the same identifiable record.

    Exact paths remain the conservative default. Facts nested under differently named
    sections can also conflict when stable record anchors identify the same record.
    Results are review signals and do not decide which source is authoritative.
    """
    claim_context_facts = _flatten_with_context(claim_content)
    evidence_context_facts = [
        fact for evidence in evidence_contents
        for fact in _flatten_with_context(evidence)
    ]

    conflicts: list[dict[str, str]] = []
    for claim_path, claim_value, claim_context in claim_context_facts:
        for evidence_path, evidence_value, evidence_context in evidence_context_facts:
            if claim_path != evidence_path or claim_value == evidence_value:
                continue
            has_record_context = bool(
                set(claim_context).intersection(_RECORD_ANCHORS)
                or set(evidence_context).intersection(_RECORD_ANCHORS)
            )
            has_list_index = any(part.isdigit() for part in claim_path)
            if (
                has_list_index
                and has_record_context
                and not _same_record_context(claim_context, evidence_context)
            ):
                continue
            conflicts.append({
                "field": ".".join(claim_path),
                "claim_value": claim_value,
                "evidence_values": evidence_value,
            })

    contextual_conflicts: dict[tuple[str, str, str], set[str]] = {}
    for claim_path, claim_value, claim_context in claim_context_facts:
        if not claim_path:
            continue
        canonical_field = claim_path[-1]
        for evidence_path, evidence_value, evidence_context in evidence_context_facts:
            if not evidence_path or evidence_path[-1] != canonical_field:
                continue
            if claim_value == evidence_value:
                continue
            if not _same_record_context(claim_context, evidence_context):
                continue
            key = (canonical_field, claim_value, ".".join(claim_path))
            contextual_conflicts.setdefault(key, set()).add(evidence_value)

    for (canonical_field, claim_value, claim_path), evidence_values in contextual_conflicts.items():
        conflicts.append({
            "field": canonical_field,
            "claim_value": claim_value,
            "evidence_values": " | ".join(sorted(evidence_values)),
            "claim_path": claim_path,
        })

    deduplicated: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for conflict in conflicts:
        key = (
            conflict.get("field", ""),
            conflict.get("claim_value", ""),
            conflict.get("evidence_values", ""),
        )
        if key not in seen:
            seen.add(key)
            deduplicated.append(conflict)
    return deduplicated
