from typing import Any


def _first_text(raw: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if value is not None:
            normalized = str(value).strip()
            if normalized:
                return normalized
    return None


def _optional_number(raw: dict[str, Any], key: str) -> float | None:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def normalize_raw_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Map source-provided fields to the shared job shape without inventing values."""
    return {
        "canonical_title": _first_text(raw, "title", "job_title"),
        "company_name": _first_text(raw, "company_name", "company", "employer"),
        "application_url": _first_text(raw, "application_url", "apply_url", "url"),
        "job_description": _first_text(
            raw, "job_description", "description", "job_description_text", "description_text"
        ),
        "employment_type": _first_text(raw, "employment_type", "job_type"),
        "salary_min": _optional_number(raw, "salary_min"),
        "salary_max": _optional_number(raw, "salary_max"),
        "salary_currency": _first_text(raw, "salary_currency", "currency"),
        "salary_period": _first_text(raw, "salary_period", "pay_period"),
        "salary_type": _first_text(raw, "salary_type"),
        "original_salary_text": _first_text(raw, "original_salary_text", "salary_text", "salary"),
        "location_country": _first_text(raw, "location_country", "country"),
        "location_region": _first_text(raw, "location_region", "region", "state"),
        "location_city": _first_text(raw, "location_city", "city"),
        "work_mode": _first_text(raw, "work_mode"),
        "remote_eligibility": _first_text(raw, "remote_eligibility"),
        "original_location_text": _first_text(raw, "original_location_text", "location"),
    }
