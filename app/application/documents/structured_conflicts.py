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


def find_structured_conflicts(extracted_fields: Any, evidence_rows: list[Any]) -> list[dict]:
    """Compare extracted single-value candidate facts with current approved evidence."""
    raw_fields = extracted_fields.get("fields", []) if isinstance(extracted_fields, dict) else []
    if not isinstance(raw_fields, list):
        return []
    extracted: dict[str, tuple[str, str]] = {}
    for field in raw_fields:
        if not isinstance(field, dict):
            continue
        canonical = canonical_field_name(field.get("name"))
        if canonical is None:
            continue
        normalized = _normalized_value(canonical, field.get("value"))
        if normalized:
            extracted.setdefault(canonical, (str(field.get("value")).strip(), normalized))

    conflicts: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
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
            incoming = extracted.get(canonical)
            if incoming is None or incoming[1] == existing_normalized:
                continue
            identity = (canonical, str(evidence.id), existing_normalized)
            if identity in seen:
                continue
            seen.add(identity)
            conflicts.append({
                "code": "POSSIBLE_FIELD_CONFLICT",
                "field": canonical,
                "evidence_id": str(evidence.id),
                "evidence_type": getattr(evidence, "evidence_type", None),
                "imported_value": incoming[0],
                "existing_value": existing_value,
                "message": "A structured candidate fact differs from approved evidence; review both values before saving.",
            })
    return conflicts
