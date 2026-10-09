import hashlib
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
_WHITESPACE = re.compile(r"\\s+")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = _WHITESPACE.sub(" ", str(value)).strip()
    return text or None


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
    port = parts.port
    netloc = host
    if port and not ((parts.scheme.lower() == "http" and port == 80) or
                     (parts.scheme.lower() == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = parts.path.rstrip("/") or "/"
    query = urlencode(sorted(
        (key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower().startswith("utm_") is False and key.lower() not in _TRACKING_KEYS
    ))
    return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))


def normalize_raw_record(raw: dict[str, Any]) -> dict[str, Any]:
    title = _clean_text(raw.get("title") or raw.get("job_title") or raw.get("position"))
    company = _clean_text(raw.get("company_name") or raw.get("company") or raw.get("employer"))
    url = normalize_url(raw.get("application_url") or raw.get("url") or raw.get("job_url"))
    description = _clean_text(
        raw.get("description") or raw.get("job_description") or raw.get("description_text")
    )
    location = _clean_text(raw.get("location") or raw.get("location_text"))
    normalized = {
        "canonical_title": title or "",
        "company_name": company,
        "application_url": url,
        "employment_type": _clean_text(raw.get("employment_type") or raw.get("job_type")),
        "location_country": _clean_text(raw.get("location_country") or raw.get("country")),
        "location_region": _clean_text(raw.get("location_region") or raw.get("region")),
        "location_city": _clean_text(raw.get("location_city") or raw.get("city")),
        "work_mode": _clean_text(raw.get("work_mode") or raw.get("remote_type")),
        "remote_eligibility": _clean_text(raw.get("remote_eligibility")),
        "original_location_text": location,
        "description": description,
    }
    normalized["title_key"] = (title or "").casefold()
    normalized["company_key"] = (company or "").casefold()
    normalized["description_hash"] = (
        hashlib.sha256(description.encode("utf-8")).hexdigest() if description else None
    )
    normalized["identity_fingerprint"] = build_identity_fingerprint(normalized)
    normalized["description_completeness"] = assess_description_completeness(description)
    return normalized


def build_identity_fingerprint(job: dict[str, Any]) -> str:
    """Exact normalized fingerprint. It is a hint, not permission to fuzzy-merge jobs."""
    parts = [
        _clean_text(job.get("canonical_title") or job.get("title")) or "",
        _clean_text(job.get("company_name") or job.get("company")) or "",
        _clean_text(job.get("location_city")) or "",
        _clean_text(job.get("location_region")) or "",
        _clean_text(job.get("location_country")) or "",
        _clean_text(job.get("description")) or "",
    ]
    canonical = "\\x1f".join(part.casefold() for part in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assess_description_completeness(description: str | None) -> str:
    """Deterministic minimum-content check; not a semantic claim that all requirements exist."""
    if not description or len(description.strip()) < 300:
        return "INCOMPLETE"
    words = re.findall(r"\\b[\\w+#.-]+\\b", description)
    if len(words) < 50:
        return "INCOMPLETE"
    return "USABLE"
