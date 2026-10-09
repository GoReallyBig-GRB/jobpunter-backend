import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID


class IdentityAction(StrEnum):
    MATCH_EXISTING = "MATCH_EXISTING"
    CREATE_NEW = "CREATE_NEW"
    REVIEW = "REVIEW"


@dataclass(frozen=True)
class JobIdentityCandidate:
    source_id: UUID | None
    external_record_id: str | None
    application_url: str | None
    title: str | None
    company_name: str | None
    location_city: str | None = None
    location_country: str | None = None
    description_fingerprint: str | None = None


@dataclass(frozen=True)
class ExistingJobIdentity:
    job_id: UUID
    application_url: str | None
    title: str | None
    company_name: str | None
    location_city: str | None = None
    location_country: str | None = None
    description_fingerprint: str | None = None
    source_identifiers: tuple[tuple[UUID, str], ...] = ()


@dataclass(frozen=True)
class IdentityResolutionDecision:
    action: IdentityAction
    job_id: UUID | None
    method: str
    confidence: float
    evidence: tuple[str, ...]


def normalize_identity_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value).strip().casefold()
    return normalized or None


def normalize_application_url(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value.strip())
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return None
        scheme = parsed.scheme.casefold()
        host = parsed.hostname.casefold()
        port = parsed.port
        if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
            host = f"{host}:{port}"
        path = parsed.path.rstrip("/") or "/"
        return urlunsplit((scheme, host, path, parsed.query, ""))
    except ValueError:
        return None


def description_fingerprint(value: str | None) -> str | None:
    normalized = normalize_identity_text(value)
    if not normalized:
        return None
    return sha256(normalized.encode("utf-8")).hexdigest()


def resolve_job_identity(
    candidate: JobIdentityCandidate,
    existing_jobs: Iterable[ExistingJobIdentity],
) -> IdentityResolutionDecision:
    """Resolve only on strong deterministic evidence; weak collisions require review."""
    existing = list(existing_jobs)
    source_id = candidate.source_id
    external_id = normalize_identity_text(candidate.external_record_id)

    if source_id and external_id:
        matches = [
            job for job in existing
            if any(
                known_source == source_id and normalize_identity_text(known_id) == external_id
                for known_source, known_id in job.source_identifiers
            )
        ]
        if len(matches) == 1:
            return IdentityResolutionDecision(
                IdentityAction.MATCH_EXISTING, matches[0].job_id, "SOURCE_EXTERNAL_ID", 1.0,
                ("same source identifier",),
            )
        if len(matches) > 1:
            return IdentityResolutionDecision(
                IdentityAction.REVIEW, None, "AMBIGUOUS_SOURCE_EXTERNAL_ID", 0.0,
                tuple(str(job.job_id) for job in matches),
            )

    url = normalize_application_url(candidate.application_url)
    if url:
        matches = [
            job for job in existing
            if normalize_application_url(job.application_url) == url
        ]
        if len(matches) == 1:
            return IdentityResolutionDecision(
                IdentityAction.MATCH_EXISTING, matches[0].job_id, "APPLICATION_URL", 0.99,
                ("normalized application URL matches",),
            )
        if len(matches) > 1:
            return IdentityResolutionDecision(
                IdentityAction.REVIEW, None, "AMBIGUOUS_APPLICATION_URL", 0.0,
                tuple(str(job.job_id) for job in matches),
            )

    title = normalize_identity_text(candidate.title)
    company = normalize_identity_text(candidate.company_name)
    city = normalize_identity_text(candidate.location_city)
    country = normalize_identity_text(candidate.location_country)
    fingerprint = candidate.description_fingerprint

    if not title or not company:
        return IdentityResolutionDecision(
            IdentityAction.REVIEW, None, "INSUFFICIENT_IDENTITY_FIELDS", 0.0,
            tuple(field for field, value in (("title", title), ("company", company)) if not value),
        )

    strong_matches = [
        job for job in existing
        if normalize_identity_text(job.title) == title
        and normalize_identity_text(job.company_name) == company
        and fingerprint
        and job.description_fingerprint == fingerprint
    ]
    if len(strong_matches) == 1:
        return IdentityResolutionDecision(
            IdentityAction.MATCH_EXISTING, strong_matches[0].job_id, "DESCRIPTION_FINGERPRINT",
            0.95, ("title matches", "company matches", "description fingerprint matches"),
        )
    if len(strong_matches) > 1:
        return IdentityResolutionDecision(
            IdentityAction.REVIEW, None, "AMBIGUOUS_DESCRIPTION_FINGERPRINT", 0.0,
            tuple(str(job.job_id) for job in strong_matches),
        )

    weak_matches = [
        job for job in existing
        if normalize_identity_text(job.title) == title
        and normalize_identity_text(job.company_name) == company
        and (not city or normalize_identity_text(job.location_city) == city)
        and (not country or normalize_identity_text(job.location_country) == country)
    ]
    if weak_matches:
        return IdentityResolutionDecision(
            IdentityAction.REVIEW, None, "TITLE_COMPANY_LOCATION_ONLY", 0.5,
            tuple(str(job.job_id) for job in weak_matches),
        )

    return IdentityResolutionDecision(
        IdentityAction.CREATE_NEW, None, "NO_STRONG_MATCH", 1.0,
        ("no matching external ID, application URL, or description fingerprint",),
    )
