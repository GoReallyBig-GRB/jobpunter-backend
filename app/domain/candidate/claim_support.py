"""Conservative deterministic checks that candidate claim facts match evidence."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


_KEY_ALIASES = {
    "company": "employer",
    "organization": "employer",
    "employer": "employer",
    "position": "role",
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
    "university": "institution",
    "institution": "institution",
    "degree": "qualification",
    "qualification": "qualification",
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



def claim_fact_conflicts_with_evidence(
    claim_content: Mapping[str, Any],
    evidence_contents: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Identify same-field values that disagree, without guessing across fields.

    This is a conservative conflict signal for human review, not a verdict about
    which source is true. Facts without a matching field path are not conflicts.
    """
    claim_facts = _flatten(claim_content)
    evidence_by_path: dict[tuple[str, ...], set[str]] = {}
    for evidence in evidence_contents:
        for path, value in _flatten(evidence):
            evidence_by_path.setdefault(path, set()).add(value)

    conflicts: list[dict[str, str]] = []
    for path, claim_value in claim_facts:
        competing_values = sorted(
            value for value in evidence_by_path.get(path, set())
            if value != claim_value
        )
        if competing_values:
            conflicts.append({
                "field": ".".join(path),
                "claim_value": claim_value,
                "evidence_values": " | ".join(competing_values),
            })
    return conflicts
