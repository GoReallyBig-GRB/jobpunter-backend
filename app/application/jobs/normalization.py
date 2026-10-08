from typing import Any

def normalize_raw_record(raw: dict[str, Any]) -> dict[str, Any]:
    title = str(raw.get("title") or raw.get("job_title") or "").strip()
    company = str(raw.get("company_name") or raw.get("company") or "").strip()
    url = raw.get("application_url") or raw.get("url")
    return {"canonical_title": title, "company_name": company,
            "application_url": str(url).strip() if url else None,
            "employment_type": raw.get("employment_type"),
            "location_country": raw.get("location_country"),
            "location_region": raw.get("location_region"),
            "location_city": raw.get("location_city"),
            "work_mode": raw.get("work_mode"),
            "remote_eligibility": raw.get("remote_eligibility"),
            "original_location_text": raw.get("location")}
