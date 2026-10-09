import hashlib
import re
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit


_TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "refid", "trackingid", "trk"}
_WHITESPACE = re.compile(r"\s+")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = _WHITESPACE.sub(" ", str(value)).strip()
    return text or None


def _first_text(raw: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _clean_text(raw.get(key))
        if value:
            return value
    return None


def _optional_number(raw: dict[str, Any], key: str) -> float | None:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def normalize_url(value: Any) -> str | None:
    """Normalize URL identity without discarding path/query data that may identify a vacancy."""
    text = _clean_text(value)
    if not text:
        return None
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return text
    host = parts.hostname.lower() if parts.hostname else ""
    if not host:
        return text
    try:
        port = parts.port
    except ValueError:
        return text
    netloc = host
    if port and not (
        (parts.scheme.lower() == "http" and port == 80)
        or (parts.scheme.lower() == "https" and port == 443)
    ):
        netloc = f"{host}:{port}"
    path = parts.path.rstrip("/") or "/"
    query = urlencode(sorted(
        (key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
    ))
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def build_identity_fingerprint(job: dict[str, Any]) -> str:
    """Build an exact normalized fingerprint; it is a hint, not permission to fuzzy-merge jobs."""
    parts = [
        _clean_text(job.get("canonical_title") or job.get("title")) or "",
        _clean_text(job.get("company_name") or job.get("company")) or "",
        _clean_text(job.get("location_city")) or "",
        _clean_text(job.get("location_region")) or "",
        _clean_text(job.get("location_country")) or "",
        _clean_text(job.get("job_description") or job.get("description")) or "",
    ]
    canonical = "\x1f".join(part.casefold() for part in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assess_description_completeness(description: str | None) -> str:
    """Minimum-content check only; it does not establish semantic completeness."""
    if not description or len(description.strip()) < 300:
        return "INCOMPLETE"
    words = re.findall(r"\b[\w+#.-]+\b", description)
    return "USABLE" if len(words) >= 50 else "INCOMPLETE"


def normalize_raw_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Map source fields to the shared job shape without inventing missing values."""
    title = _first_text(raw, "title", "job_title", "position")
    company = _first_text(raw, "company_name", "company", "employer")
    application_url = normalize_url(
        _first_text(raw, "application_url", "apply_url", "url", "job_url")
    )
    description = _first_text(
        raw, "job_description", "description", "job_description_text", "description_text"
    )
    normalized = {
        "canonical_title": title or "",
        "company_name": company,
        "application_url": application_url,
        "job_description": description,
        "description": description,
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
        "work_mode": _first_text(raw, "work_mode", "remote_type"),
        "remote_eligibility": _first_text(raw, "remote_eligibility"),
        "original_location_text": _first_text(raw, "original_location_text", "location", "location_text"),
    }
    normalized["title_key"] = (title or "").casefold()
    normalized["company_key"] = (company or "").casefold()
    normalized["description_hash"] = (
        hashlib.sha256(description.encode("utf-8")).hexdigest() if description else None
    )
    normalized["description_completeness"] = assess_description_completeness(description)
    normalized["identity_fingerprint"] = build_identity_fingerprint(normalized)
    return normalized


_EXTERNAL_UUID = re.compile(
    r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(?:/|$)",
    re.IGNORECASE,
)


def extract_external_job_id(url: str | None) -> str | None:
    """Extract known ATS/job-board IDs without guessing when the URL is invalid."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
        params = parse_qs(parts.query)
    except ValueError:
        return None

    lever_match = _EXTERNAL_UUID.search(parts.path)
    if lever_match:
        return f"lever_{lever_match.group(1)}"
    if params.get("gh_jid", [None])[0]:
        return f"greenhouse_{params['gh_jid'][0]}"
    greenhouse_match = re.search(r"/jobs/(\\d+)(?:/|$)", parts.path)
    if greenhouse_match:
        return f"greenhouse_{greenhouse_match.group(1)}"
    if params.get("currentJobId", [None])[0]:
        return f"linkedin_{params['currentJobId'][0]}"
    linkedin_match = re.search(r"/view/(\\d+)(?:/|$)", parts.path)
    if linkedin_match:
        return f"linkedin_{linkedin_match.group(1)}"
    if params.get("jk", [None])[0]:
        return f"indeed_{params['jk'][0]}"
    return None


def derive_provenance_label(source: str | None = None, url: str | None = None) -> str:
    """Map ingestion-source hints to the legacy user-facing provenance label."""
    if not source:
        return "Quick Job-Site Match"
    lowered = source.lower()
    if "scheduled" in lowered:
        return "Scheduled Job-Site Match"
    if any(term in lowered for term in ("automated", "gas", "alert api")):
        return "Automated Email Alert"
    if "email" in lowered or "alert" in lowered:
        return "Manual Email"
    if "url" in lowered or (url and url.startswith("http")):
        return "Manual URL"
    if "quick" in lowered:
        return "Quick Job-Site Match"
    return "Quick Job-Site Match"
