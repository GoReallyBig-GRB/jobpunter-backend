import hashlib
import math
from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from app.domain.jobs.completeness import has_substantive_job_description


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
    number = float(value)
    return number if math.isfinite(number) else None


def _optional_bool(raw: dict[str, Any], key: str) -> bool | None:
    value = raw.get(key)
    return value if isinstance(value, bool) else None


_SALARY_PERIOD_ALIASES = {
    "HOUR": "HOUR", "HOURLY": "HOUR", "PERHOUR": "HOUR",
    "MONTH": "MONTH", "MONTHLY": "MONTH", "PERMONTH": "MONTH",
    "YEAR": "YEAR", "YEARLY": "YEAR", "ANNUAL": "YEAR", "ANNUALLY": "YEAR",
    "PERYEAR": "YEAR", "CONTRACT": "CONTRACT", "CONTRACTOR": "CONTRACT",
}


def _optional_salary_currency(raw: dict[str, Any]) -> str | None:
    value = _first_text(raw, "salary_currency", "currency")
    return value.upper() if value and re.fullmatch(r"[A-Za-z]{3}", value) else None


def _optional_salary_period(raw: dict[str, Any]) -> str | None:
    value = _first_text(raw, "salary_period", "pay_period")
    if not value:
        return None
    key = re.sub(r"[^A-Za-z]", "", value).upper()
    return _SALARY_PERIOD_ALIASES.get(key)


_ROUTE_TYPES = {
    "DIRECT_EMPLOYER", "FREE_ATS", "FREE_AGGREGATOR",
    "PAID", "BLOCKED", "PAID_OR_BLOCKED", "UNKNOWN",
}


def normalize_application_route_type(value: Any) -> str | None:
    """Normalize an explicitly supplied route classification; never infer from a URL."""
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[-\s]+", "_", value.strip().upper())
    return normalized if normalized in _ROUTE_TYPES else None


def _optional_route_type(raw: dict[str, Any]) -> str | None:
    value = raw.get("application_route_type") or raw.get("application_route_classification")
    return normalize_application_route_type(value)


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
    # IPv6 literals must remain bracketed when reconstructed as a URL authority.
    netloc = f"[{host}]" if ":" in host else host
    if port and not (
        (parts.scheme.lower() == "http" and port == 80)
        or (parts.scheme.lower() == "https" and port == 443)
    ):
        netloc = f"{netloc}:{port}"
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
    """Require meaningful role duties and qualifications, not length alone."""
    return "USABLE" if has_substantive_job_description(description) else "INCOMPLETE"


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
        "salary_currency": _optional_salary_currency(raw),
        "salary_period": _optional_salary_period(raw),
        "salary_type": _first_text(raw, "salary_type"),
        "original_salary_text": _first_text(raw, "original_salary_text", "salary_text", "salary"),
        "seniority": _first_text(raw, "seniority", "seniority_level", "experience_level"),
        "location_country": _first_text(raw, "location_country", "country"),
        "location_region": _first_text(raw, "location_region", "region", "state"),
        "location_city": _first_text(raw, "location_city", "city"),
        "work_mode": _first_text(raw, "work_mode", "remote_type"),
        "remote_eligibility": _first_text(raw, "remote_eligibility"),
        # Eligibility facts are retained only when the source explicitly supplies
        # typed values. Missing/ambiguous values remain unknown.
        "application_fee_amount": _optional_number(raw, "application_fee_amount"),
        "application_fee_currency": _first_text(raw, "application_fee_currency"),
        "relocation_required": _optional_bool(raw, "relocation_required"),
        "application_route_type": _optional_route_type(raw),
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

    hostname = (parts.hostname or "").lower()
    if hostname == "lever.co" or hostname.endswith(".lever.co"):
        lever_match = _EXTERNAL_UUID.search(parts.path)
        if lever_match:
            return f"lever_{lever_match.group(1)}"
    if hostname == "greenhouse.io" or hostname.endswith(".greenhouse.io"):
        if params.get("gh_jid", [None])[0]:
            return f"greenhouse_{params['gh_jid'][0]}"
        greenhouse_match = re.search(r"/jobs/(\d+)(?:/|$)", parts.path)
        if greenhouse_match:
            return f"greenhouse_{greenhouse_match.group(1)}"
    if hostname == "linkedin.com" or hostname.endswith(".linkedin.com"):
        if params.get("currentJobId", [None])[0]:
            return f"linkedin_{params['currentJobId'][0]}"
        linkedin_match = re.search(r"/view/(\d+)(?:/|$)", parts.path)
        if linkedin_match:
            return f"linkedin_{linkedin_match.group(1)}"
    if hostname == "indeed.com" or hostname.endswith(".indeed.com"):
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


def evaluate_parser_confidence(input_record: dict[str, Any]) -> dict[str, Any]:
    """Port the legacy completeness confidence rule; this is not semantic validation."""
    title = _clean_text(input_record.get("title")) or ""
    company = _clean_text(input_record.get("company")) or ""
    job_url = _clean_text(input_record.get("jobUrl") or input_record.get("job_url")) or ""
    description = _clean_text(
        input_record.get("description") or input_record.get("job_description")
    ) or ""
    missing: list[str] = []
    if not title:
        missing.append("Job Title")
    if not company:
        missing.append("Company Name")
    if not job_url:
        missing.append("Job / Application URL")
    if not description:
        missing.append("Job Description")
    elif len(description) < 80:
        missing.append("Meaningful Job Description (content too brief to assess)")

    if not missing:
        confidence = "HIGH"
    elif len(missing) == 1 and "Job Title" not in missing and "Company Name" not in missing:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    return {"confidence": confidence, "missingFields": missing}


def determine_jd_retrieval_status(input_record: dict[str, Any]) -> str:
    """Port the legacy JD retrieval status rule while preserving explicit upstream status."""
    explicit = input_record.get("jdRetrievalStatus") or input_record.get("jd_retrieval_status")
    if explicit:
        return str(explicit)
    description = _clean_text(
        input_record.get("description") or input_record.get("job_description")
    ) or ""
    url = _clean_text(input_record.get("jobUrl") or input_record.get("job_url")) or ""
    if re.search(r"closed|no longer accepting", description, re.IGNORECASE):
        return "CLOSED"
    if not url:
        return "REQUIRES_REVIEW"
    if len(description) > 300:
        return "FULL_JD_RETRIEVED"
    if len(description) > 80:
        return "PARTIAL_JD_RETRIEVED"
    return "JD_UNAVAILABLE"


def evaluate_freshness(
    posting_date: str | None = None,
    description: str | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Port freshness scoring with deterministic clock injection and safe invalid-date handling."""
    text = (description or "").lower()
    if re.search(
        r"this job is closed|no longer accepting applications|expired job|position filled",
        text,
        re.IGNORECASE,
    ):
        return {
            "status": "CLOSED_EXPIRED",
            "postingDate": posting_date or "Unknown",
            "warning": "Role is explicitly marked as closed or no longer accepting applications.",
            "score": 0,
        }
    if not posting_date:
        return {
            "status": "AGE_UNCERTAIN",
            "postingDate": "Not specified",
            "warning": "Posting date was not extracted from alert. Verify role is still active before applying.",
            "score": 6,
        }

    parsed_date: datetime | None = None
    candidate = posting_date.strip()
    for fmt in (None, "%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%d %b %Y"):
        try:
            if fmt is None:
                parsed_date = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            else:
                parsed_date = datetime.strptime(candidate, fmt)
            break
        except ValueError:
            continue
    if parsed_date is None:
        return {
            "status": "AGE_UNCERTAIN",
            "postingDate": posting_date,
            "warning": "Unparseable date format.",
            "score": 6,
        }

    current = now or datetime.now(timezone.utc)
    if parsed_date.tzinfo is None:
        parsed_date = parsed_date.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    days = abs((current - parsed_date).total_seconds())
    diff_days = int((days + 86_399) // 86_400)
    if diff_days > 30:
        return {
            "status": "STALE",
            "postingDate": posting_date,
            "daysSincePosted": diff_days,
            "warning": f"Posting is {diff_days} days old (>30 days). Likely in advanced interviewing stages or stale.",
            "score": 3,
        }
    if diff_days <= 7:
        return {
            "status": "RECENTLY_POSTED",
            "postingDate": posting_date,
            "daysSincePosted": diff_days,
            "score": 10,
        }
    return {
        "status": "CURRENT_ACTIVE",
        "postingDate": posting_date,
        "daysSincePosted": diff_days,
        "score": 8,
    }


def evaluate_application_route(
    job_url: str | None,
    application_url: str | None,
    description: str | None,
    preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Port legacy application-route checks; only explicit fee evidence triggers a hard reject."""
    prefs = preferences or {}
    url_combined = f"{job_url or ''} {application_url or ''}".lower()
    desc_lower = (description or "").lower()
    is_paid = bool(
        re.search(
            r"pay to apply|application fee|paid subscription required|premium members only|"
            r"remote rocketship subscription|fee of \$|fee of €|unlock application|deposit required",
            desc_lower,
            re.IGNORECASE,
        )
        or re.search(r"subscribe.*to apply", desc_lower, re.IGNORECASE)
        or "paid-remote-jobs" in url_combined
    )
    max_fee = prefs.get("maxApplicationFee", prefs.get("max_application_fee", 0))
    if is_paid and max_fee == 0:
        return {
            "status": "HARD_REJECT",
            "routeType": "Paid Application Gate / Aggregator Paywall",
            "evidence": "Job listing or URL explicitly demands payment or subscription to submit an application.",
            "verificationStatus": "Blocked ($0 application policy violation)",
            "score": 0,
        }

    if re.search(
        r"greenhouse\.io|lever\.co|workable\.com|ashbyhq\.com|bamboohr\.com|"
        r"smartrecruiters\.com|applytojob\.com|jobvite\.com|recruitee\.com|rippling\.com",
        url_combined,
        re.IGNORECASE,
    ):
        destination = url_combined.split("?", 1)[0].split("/")
        origin = "/".join(destination[:3])
        return {
            "status": "GOOD",
            "routeType": "Direct Free Enterprise ATS (Greenhouse / Lever / Ashby / Workable)",
            "evidence": f"Direct enterprise ATS portal ({origin})",
            "verificationStatus": "Verified Free Route",
            "score": 10,
        }
    if re.search(
        r"linkedin\.com/jobs|indeed\.com|jobberman\.com|weworkremotely\.com|wellfound\.com",
        url_combined,
        re.IGNORECASE,
    ):
        return {
            "status": "GOOD",
            "routeType": "Reputable Free Job Platform",
            "evidence": "Direct listing on legitimate free recruitment board.",
            "verificationStatus": "Free Platform Listing",
            "score": 8,
        }
    if "careers" in url_combined or "jobs." in url_combined:
        return {
            "status": "GOOD",
            "routeType": "Official Employer Careers Page",
            "evidence": "Hosted on company's verified domain careers subpage.",
            "verificationStatus": "Verified Employer Route",
            "score": 9,
        }
    if not url_combined.strip():
        return {
            "status": "REVIEW",
            "routeType": "Missing Application Destination",
            "evidence": "No valid job or ATS URL supplied.",
            "verificationStatus": "Unverified (Requires manual destination check)",
            "score": 2,
        }
    return {
        "status": "REVIEW",
        "routeType": "Third-party Aggregator / Unknown Redirect",
        "evidence": "URL directs through an unfamiliar aggregator or redirect link.",
        "verificationStatus": "Unverified Third-party Route",
        "score": 5,
    }


def evaluate_location_and_work_auth(
    location: str | None,
    description: str | None,
    preferences: dict[str, Any],
) -> dict[str, Any]:
    """Port legacy location/work-authorization gates using candidate-owned preferences."""
    text = f"{location or ''} {description or ''}".lower()
    eligible_countries = [
        str(country).lower() for country in preferences.get("eligibleCountries", preferences.get("eligible_countries", []))
    ]
    work_auths = [
        str(auth).lower() for auth in preferences.get("workAuthorizations", preferences.get("work_authorizations", []))
    ]
    requires_us = bool(re.search(
        r"us citizen|u\.s\. citizen|united states citizen only|green card only|"
        r"must be located in the us|us work authorization required", text, re.IGNORECASE
    ))
    requires_uk = bool(re.search(
        r"uk right to work|uk citizen only|must be based in the uk", text, re.IGNORECASE
    ))
    requires_eu = bool(re.search(
        r"eu right to work|eu passport|must be resident in eu", text, re.IGNORECASE
    ))
    has_us = any("us" in auth or "united states" in auth for auth in work_auths)
    has_uk = any("uk" in auth or "united kingdom" in auth for auth in work_auths)
    has_eu = any("eu" in auth or "european" in auth for auth in work_auths)

    if requires_us and not has_us and not re.search(r"open to international|worldwide", text):
        return {
            "eligibility": "REGION_RESTRICTED", "score": 0,
            "notes": "Mandatory US work authorization required. Candidate does not possess US work authorization.",
            "hardReject": True,
            "hardNoGoReason": "Mandatory work authorization restricted to US citizens/residents.",
        }
    if requires_uk and not has_uk and not re.search(r"worldwide", text):
        return {
            "eligibility": "REGION_RESTRICTED", "score": 0,
            "notes": "Mandatory UK work authorization required. Candidate does not possess UK right-to-work.",
            "hardReject": True,
            "hardNoGoReason": "Mandatory work authorization restricted to UK residents.",
        }
    if requires_eu and not has_eu and not re.search(r"worldwide", text):
        return {
            "eligibility": "REGION_RESTRICTED", "score": 0,
            "notes": "Mandatory EU right to work required.",
            "hardReject": True,
            "hardNoGoReason": "Mandatory work authorization restricted to EU residents.",
        }

    matches_country = any(country and country in text for country in eligible_countries)
    worldwide = bool(re.search(
        r"remote.*(worldwide|global|anywhere)|worldwide remote|work from anywhere|"
        r"all countries welcome|open to international", text, re.IGNORECASE
    ))
    generic_remote = bool(
        re.search(r"remote", location or "", re.IGNORECASE)
        or re.search(r"100% remote|fully remote", text, re.IGNORECASE)
    )
    if matches_country:
        return {
            "eligibility": "NIGERIA_OK", "score": 20,
            "notes": "Direct match: Role is based in or explicitly welcomes candidates in "
            + ", ".join(str(c) for c in preferences.get("eligibleCountries", preferences.get("eligible_countries", [])))
            + ".",
            "hardReject": False,
        }
    if worldwide:
        return {
            "eligibility": "WORLDWIDE_REMOTE", "score": 18,
            "notes": "Worldwide Remote: Role explicitly welcomes global international candidates.",
            "hardReject": False,
        }
    remote_preference = preferences.get("remotePreference", preferences.get("remote_preference"))
    if generic_remote and remote_preference in {"REMOTE_ONLY", "REMOTE_OK"}:
        return {
            "eligibility": "UNCLEAR", "score": 10,
            "notes": "Listed as 'Remote' without stating international geographic eligibility. Conservative check requires human verification.",
            "hardReject": False,
        }

    foreign_onsite = bool(re.search(
        r"san francisco|new york|london|berlin|toronto|sydney|dublin|amsterdam|singapore",
        text, re.IGNORECASE
    ))
    willing_to_relocate = preferences.get("willingToRelocate", preferences.get("willing_to_relocate", False))
    if foreign_onsite and not generic_remote and not matches_country and not willing_to_relocate:
        return {
            "eligibility": "HYBRID_ONSITE", "score": 0,
            "notes": "On-site or hybrid role in foreign jurisdiction. Candidate is not willing to relocate without remote allowance.",
            "hardReject": True,
            "hardNoGoReason": "On-site foreign location requirement without international remote allowance.",
        }
    return {
        "eligibility": "UNCLEAR", "score": 8,
        "notes": "Location eligibility unclear. Verify before applying.",
        "hardReject": False,
    }


def evaluate_seniority(
    title: str | None,
    description: str | None,
    master_profile: dict[str, Any],
    preferences: dict[str, Any],
) -> dict[str, Any]:
    """Port legacy seniority/scope checks; no AI-derived seniority inference is used."""
    title_text = title or ""
    title_lower = title_text.lower()
    desc_lower = (description or "").lower()
    profile_data = master_profile.get("structuredData") or master_profile.get("structured_data") or {}
    ownership = profile_data.get("leadershipOwnership") or profile_data.get("leadership_ownership") or []
    candidate_profession = master_profile.get("primaryProfession") or master_profile.get("primary_profession") or ""
    candidate_level = (
        f"{candidate_profession} with documented experience in: "
        + ("; ".join(str(item) for item in ownership) if ownership else "Squad / Operational Ownership")
    )
    executive_title = bool(re.search(r"director|vp of|vice president|c-level|chief|head of", title_lower))
    massive_team = bool(re.search(
        r"manage a team of (?:1[0-9]|[2-9][0-9])|supervising directors|managing 15\+|managing 20\+",
        desc_lower,
    ))
    extreme_years = bool(re.search(r"(?:12\+|15\+|20\+)\s*years", desc_lower))
    if executive_title and (massive_team or extreme_years):
        return {
            "requiredSeniority": "Executive Director / VP managing large multi-tier department with 12+ years required",
            "candidateDocumentedLevel": candidate_level,
            "seniorityGap": "Materially excessive management scope (requires supervising multiple management tiers or institutional governance). Exceeds candidate's documented level.",
            "isMaterial": True,
            "decisionImpact": "HARD_NO_GO: Organizational hierarchy materially exceeds candidate profile.",
            "score": 2,
        }
    if executive_title:
        return {
            "requiredSeniority": "Director / Head of (Scope unverified)",
            "candidateDocumentedLevel": candidate_level,
            "seniorityGap": "Executive title. Requires manual verification of whether the role is hands-on or organizational management.",
            "isMaterial": False,
            "decisionImpact": "REVIEW: Check actual squad vs organizational management responsibility.",
            "score": 8,
        }
    target_levels = preferences.get("targetSeniority", preferences.get("target_seniority", []))
    matches = any(str(level).lower() in title_lower for level in target_levels)
    return {
        "requiredSeniority": title_text,
        "candidateDocumentedLevel": candidate_level,
        "seniorityGap": "Direct match with candidate's target seniority preferences." if matches else "Within realistic professional execution scope.",
        "isMaterial": False,
        "decisionImpact": "SUPPORTED: Scope aligns with candidate's documented experience.",
        "score": 15,
    }


def candidate_excludes_sales(preferences: dict[str, Any]) -> bool:
    """Port the legacy explicit exclusion check for outbound quota-carrying sales roles."""
    excluded_types = preferences.get("excludedJobTypes", preferences.get("excluded_job_types", [])) or []
    excluded_industries = preferences.get("excludedIndustries", preferences.get("excluded_industries", [])) or []
    return any(
        re.search(r"sales|sdr|bdr|cold call|commission", str(value), re.IGNORECASE)
        for value in [*excluded_types, *excluded_industries]
    )


def detect_duplicates(new_job: dict[str, Any], existing_jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Port legacy duplicate detection: external ID, normalized URL, then company/title."""
    def value(record: dict[str, Any], *keys: str) -> str:
        for key in keys:
            item = record.get(key)
            if item is not None:
                return str(item)
        return ""

    def normalized(text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", text.lower())

    title = value(new_job, "title")
    company = value(new_job, "company", "company_name")
    normalized_title = normalized(title)
    normalized_company = normalized(company)
    clean_url = value(new_job, "cleanUrl", "clean_url") or (
        normalize_url(value(new_job, "jobUrl", "job_url")) or ""
    )
    external_id = value(new_job, "externalJobId", "external_job_id")

    for existing in existing_jobs:
        if value(existing, "id") == value(new_job, "id"):
            continue
        existing_external_id = value(existing, "externalJobId", "external_job_id")
        if external_id and existing_external_id and external_id == existing_external_id:
            return {
                "isDuplicate": True,
                "duplicateOf": existing,
                "reason": f"Identical external job posting ID ({external_id})",
            }

        existing_title = value(existing, "title")
        existing_company = value(existing, "company", "company_name")
        existing_normalized_title = normalized(existing_title)
        existing_normalized_company = normalized(existing_company)
        existing_clean_url = value(existing, "cleanUrl", "clean_url") or (
            normalize_url(value(existing, "jobUrl", "job_url")) or ""
        )
        if clean_url and existing_clean_url and clean_url == existing_clean_url:
            return {
                "isDuplicate": True,
                "duplicateOf": existing,
                "reason": "Exact job URL match (after stripping tracking parameters)",
            }
        if normalized_company and existing_normalized_company and normalized_company == existing_normalized_company:
            title_matches = (
                normalized_title == existing_normalized_title
                or (len(normalized_title) > 10 and normalized_title in existing_normalized_title)
                or (len(existing_normalized_title) > 10 and existing_normalized_title in normalized_title)
            )
            if title_matches:
                return {
                    "isDuplicate": True,
                    "duplicateOf": existing,
                    "reason": f"Same employer '{existing_company}' matching title '{existing_title}'",
                }
    return {"isDuplicate": False}
