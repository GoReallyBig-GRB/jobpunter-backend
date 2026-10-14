"""Conservative structured conflict detection for candidate document imports.

Only fields that should normally have one candidate-level value are compared.
Differences are review signals, never automatic evidence rejection.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

_FIELD_ALIASES = {
    "full_name": {"name", "full_name", "candidate_name", "legal_name"},
    "email": {"email", "email_address", "contact_email"},
    "phone": {"phone", "phone_number", "mobile", "mobile_number", "contact_phone"},
    "date_of_birth": {"date_of_birth", "birth_date", "dob"},
    "linkedin_url": {"linkedin", "linkedin_url", "linkedin_profile"},
}
_ALIAS_TO_FIELD = {
    re.sub(r"[^a-z0-9]", "", alias): canonical
    for canonical, aliases in _FIELD_ALIASES.items()
    for alias in aliases
}


def canonical_field_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    return _ALIAS_TO_FIELD.get(normalized)


def _normalized_value(field_name: str, value: Any) -> str | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    text = str(value).strip().casefold()
    if not text:
        return None
    if field_name == "email":
        return text
    if field_name == "phone":
        digits = re.sub(r"\D", "", text)
        return digits or None
    if field_name == "linkedin_url":
        text = re.sub(r"^https?://", "", text)
        text = text.removeprefix("www.")
        return text.rstrip("/")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _structured_values(value: Any):
    if isinstance(value, dict):
        # Document-import extraction uses {name, value, source_text, confidence}
        # descriptors. Interpret the name as the field selector, not as a person's name.
        if isinstance(value.get("name"), str) and "value" in value and (
            "source_text" in value or "confidence" in value
        ):
            canonical = canonical_field_name(value["name"])
            normalized = _normalized_value(canonical, value.get("value")) if canonical else None
            if canonical is not None and normalized:
                yield canonical, str(value["value"]).strip(), normalized
            return
        for key, child in value.items():
            canonical = canonical_field_name(key)
            if canonical is not None:
                normalized = _normalized_value(canonical, child)
                if normalized:
                    yield canonical, str(child).strip(), normalized
            if isinstance(child, (dict, list)):
                yield from _structured_values(child)
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, (dict, list)):
                yield from _structured_values(child)


def find_structured_conflicts(
    extracted_fields: Any,
    evidence_rows: list[Any],
    profile_data: Any = None,
) -> list[dict]:
    """Compare imported candidate facts with profile identity and current approved evidence.

    Conflicts are review signals, not a verdict about which source is correct.
    Multiple distinct imported values for a normally single-value identity field are
    also surfaced instead of silently accepting the first extracted value.
    """
    raw_fields = extracted_fields.get("fields", []) if isinstance(extracted_fields, dict) else []
    if not isinstance(raw_fields, list):
        return []
    extracted: dict[str, list[tuple[str, str, str]]] = {}
    for field in raw_fields:
        if not isinstance(field, dict):
            continue
        canonical = canonical_field_name(field.get("name"))
        if canonical is None:
            continue
        normalized = _normalized_value(canonical, field.get("value"))
        if normalized:
            extracted.setdefault(canonical, []).append((
                str(field.get("value")).strip(),
                normalized,
                str(field.get("source_text") or "").strip(),
            ))

    conflicts: list[dict] = []
    for canonical, values in extracted.items():
        distinct = {}
        for display_value, normalized, source_text in values:
            distinct.setdefault(normalized, {"value": display_value, "source_texts": []})
            if source_text and source_text not in distinct[normalized]["source_texts"]:
                distinct[normalized]["source_texts"].append(source_text)
        if len(distinct) > 1:
            conflicts.append({
                "code": "CONFLICTING_IMPORTED_VALUES",
                "field": canonical,
                "imported_values": [item["value"] for item in distinct.values()],
                "source_texts": [source for item in distinct.values() for source in item["source_texts"]],
                "message": "The imported document contains different values for the same candidate identity field; review the source text before saving.",
            })

    identity = profile_data.get("identity", {}) if isinstance(profile_data, dict) else {}
    profile_values: dict[str, list[tuple[str, str]]] = {}
    for canonical, display_value, normalized in _structured_values(identity):
        profile_values.setdefault(canonical, []).append((display_value, normalized))

    now = datetime.now(timezone.utc)
    for evidence in evidence_rows:
        if getattr(evidence, "status", None) != "APPROVED":
            continue
        effective_from = getattr(evidence, "effective_from", None)
        effective_to = getattr(evidence, "effective_to", None)
        expires_at = getattr(evidence, "expires_at", None)
        dates = []
        for value in (effective_from, effective_to, expires_at):
            if value is not None and value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            dates.append(value)
        effective_from, effective_to, expires_at = dates
        if (
            (effective_from and effective_from > now)
            or (effective_to and effective_to <= now)
            or (expires_at and expires_at <= now)
        ):
            continue
        content = getattr(evidence, "content", None)
        for canonical, existing_value, existing_normalized in _structured_values(content):
            incoming_values = extracted.get(canonical, [])
            for incoming_value, incoming_normalized, _source_text in incoming_values:
                if incoming_normalized == existing_normalized:
                    continue
                conflicts.append({
                    "code": "POSSIBLE_FIELD_CONFLICT",
                    "field": canonical,
                    "evidence_id": str(evidence.id),
                    "evidence_type": getattr(evidence, "evidence_type", None),
                    "imported_value": incoming_value,
                    "existing_value": existing_value,
                    "message": "A structured candidate fact differs from approved evidence; review both values before saving.",
                })

    for canonical, existing_values in profile_values.items():
        for incoming_value, incoming_normalized, source_text in extracted.get(canonical, []):
            for existing_value, existing_normalized in existing_values:
                if incoming_normalized == existing_normalized:
                    continue
                conflicts.append({
                    "code": "POSSIBLE_PROFILE_FIELD_CONFLICT",
                    "field": canonical,
                    "imported_value": incoming_value,
                    "existing_value": existing_value,
                    "source_text": source_text or None,
                    "message": "An imported identity fact differs from the saved Master Career Profile; review both values before saving.",
                })

    # Stable de-duplication is important when evidence repeats the same fact in
    # multiple nested structures.
    deduplicated = []
    seen: set[tuple[str, str, str, str]] = set()
    for conflict in conflicts:
        key = (
            str(conflict.get("code")),
            str(conflict.get("field")),
            str(conflict.get("evidence_id") or ""),
            str(conflict.get("imported_value") or conflict.get("imported_values") or ""),
        )
        if key not in seen:
            seen.add(key)
            deduplicated.append(conflict)
    return deduplicated
