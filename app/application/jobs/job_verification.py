"""Persist auditable outcomes from a configured job-application verifier.

Network access belongs behind the verifier callable so deployments can apply their
own egress policy and tests never need to contact arbitrary URLs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import Job, JobVerificationAttempt


VERIFICATION_STATUSES = {"ACTIVE", "CLOSED", "UNKNOWN"}
VERIFIER_VERSION = "job-verification-v1"
VERIFICATION_FRESHNESS_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class VerificationResult:
    status: str
    checked_url: str
    final_url: str | None
    http_status_code: int | None
    reason_code: str
    evidence: Mapping[str, Any]


Verifier = Callable[[str], VerificationResult]


def _validate_web_url(value: str | None, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty URL or None")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{field} must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field} must not contain embedded credentials")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{field} has an invalid port") from exc


def record_job_verification(
    db: Session,
    job_id: UUID,
    result: VerificationResult,
    *,
    now: datetime | None = None,
    verifier_version: str = VERIFIER_VERSION,
    commit: bool = True,
) -> JobVerificationAttempt:
    """Save a verifier outcome and update job state only for explicit outcomes."""
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    status = str(result.status).upper()
    if status not in VERIFICATION_STATUSES:
        raise ValueError("status must be ACTIVE, CLOSED, or UNKNOWN")
    if status == "UNKNOWN" and result.reason_code == "URL_REJECTED_BY_SAFETY_POLICY":
        if not isinstance(result.checked_url, str) or not result.checked_url.strip():
            raise ValueError("checked_url must be a non-empty string")
        if len(result.checked_url) > 4096:
            raise ValueError("checked_url is too long to audit")
    else:
        _validate_web_url(result.checked_url, "checked_url")
    _validate_web_url(result.final_url, "final_url")
    if not isinstance(result.evidence, Mapping):
        raise ValueError("evidence must be an object")
    if not result.reason_code or len(result.reason_code) > 64:
        raise ValueError("reason_code must contain 1–64 characters")
    if result.http_status_code is not None and not 100 <= result.http_status_code <= 599:
        raise ValueError("http_status_code must be between 100 and 599")

    if status == "ACTIVE" and result.final_url is None:
        raise ValueError("ACTIVE verification requires a final_url")

    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if job is None:
        raise ValueError("job does not exist")
    if not job.application_url or result.checked_url != job.application_url:
        raise ValueError("checked_url must exactly match the job's current application_url")

    attempt = JobVerificationAttempt(
        job_id=job.id,
        checked_url=result.checked_url,
        final_url=result.final_url,
        status=status,
        http_status_code=result.http_status_code,
        reason_code=result.reason_code,
        evidence=dict(result.evidence),
        verifier_version=verifier_version,
        checked_at=current_time,
        created_at=current_time,
    )
    db.add(attempt)

    if status == "ACTIVE":
        job.last_verified_at = current_time
        job.lifecycle_status = "ACTIVE"
        job.freshness_status = "FRESH"
    elif status == "CLOSED":
        job.last_verified_at = current_time
        job.lifecycle_status = "CLOSED"
        job.freshness_status = "FRESH"
    # UNKNOWN is audited but does not refresh trust or alter lifecycle state.
    if commit:
        db.commit()
        db.refresh(attempt)
    else:
        db.flush()
    return attempt


def current_application_route_verification(
    db: Session,
    job: Job,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return only a current, URL-matched verification as active route evidence."""
    current_time = now or datetime.now(timezone.utc)
    attempt = db.scalar(
        select(JobVerificationAttempt)
        .where(JobVerificationAttempt.job_id == job.id)
        .order_by(JobVerificationAttempt.checked_at.desc(), JobVerificationAttempt.id.desc())
        .limit(1)
    )
    if attempt is None:
        return {
            "status": "UNKNOWN",
            "attempt_id": None,
            "checked_url": None,
            "final_url": None,
            "http_status_code": None,
            "reason_code": "NO_VERIFICATION_ATTEMPT",
            "verifier_version": None,
            "checked_at": None,
        }

    status = str(attempt.status).upper()
    if attempt.checked_url != job.application_url:
        status = "UNKNOWN"
    if status == "ACTIVE":
        checked_at = attempt.checked_at
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        last_verified_at = job.last_verified_at
        if last_verified_at is not None and last_verified_at.tzinfo is None:
            last_verified_at = last_verified_at.replace(tzinfo=timezone.utc)
        if (
            last_verified_at is None
            or last_verified_at != checked_at
            or checked_at < current_time - VERIFICATION_FRESHNESS_WINDOW
        ):
            status = "UNKNOWN"

    return {
        "status": status,
        "attempt_id": str(attempt.id),
        "checked_url": attempt.checked_url,
        "final_url": attempt.final_url,
        "http_status_code": attempt.http_status_code,
        "reason_code": attempt.reason_code,
        "verifier_version": attempt.verifier_version,
        "checked_at": (
            attempt.checked_at.isoformat()
            if attempt.checked_at is not None else None
        ),
    }


def verify_and_record_job(
    db: Session,
    job_id: UUID,
    verifier: Verifier,
    *,
    now: datetime | None = None,
) -> JobVerificationAttempt:
    """Run an injected verifier and persist its explicit result."""
    job = db.get(Job, job_id)
    if job is None:
        raise ValueError("job does not exist")
    if not job.application_url:
        raise ValueError("job has no application_url to verify")
    result = verifier(job.application_url)
    if not isinstance(result, VerificationResult):
        raise TypeError("verifier must return VerificationResult")
    return record_job_verification(db, job_id, result, now=now)
