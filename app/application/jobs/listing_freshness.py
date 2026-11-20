"""Listing-based freshness and closure for jobs fed by complete official board listings.

A complete, successful refresh of an official public job-board listing is authoritative:
a job that is still listed is confirmed open (FRESH, last_verified_at advanced); a job that is
absent from two consecutive complete refreshes is closed. Nothing is inferred from a partial,
failed, empty or capped fetch, so an outage can never close jobs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.db.models import (
    Job, JobDiscoveryEvent, JobProvenance, RawSourceRecord,
)

MISS_THRESHOLD = 2
CLOSED_REASON = "NOT_IN_SOURCE_LISTING"
TERMINAL_LIFECYCLE_STATES = ("CLOSED", "EXPIRED", "REMOVED")

# Official public endpoints that return every published posting in one response when no
# paging parameters are supplied.
_FULL_LISTING_PATTERNS = (
    ("api.lever.co", re.compile(r"^/v0/postings/[A-Za-z0-9._-]+/?$")),
    ("boards-api.greenhouse.io", re.compile(r"^/v1/boards/[A-Za-z0-9._-]+/jobs/?$")),
    ("api.ashbyhq.com", re.compile(r"^/posting-api/job-board/[A-Za-z0-9._-]+/?$")),
    ("apply.workable.com", re.compile(r"^/api/v1/widget/accounts/[A-Za-z0-9._-]+/?$")),
)
_PAGING_KEYS = {"limit", "skip", "offset", "page", "per_page", "page_size", "cursor"}


def is_full_listing_connector(connector_type: str, configuration: dict | None) -> bool:
    """True only for a connector known to return the source's entire listing per run."""
    kind = str(connector_type or "").strip().upper()
    if kind == "GREENHOUSE_JOB_BOARD":
        return True
    if kind != "JSON_API":
        return False
    config = configuration or {}
    params = config.get("base_params") or {}
    if config.get("page_param") or config.get("page_size_param"):
        return False
    if isinstance(params, dict) and _PAGING_KEYS & {str(k).lower() for k in params}:
        return False
    parts = urlsplit(str(config.get("endpoint_url") or ""))
    if parts.scheme != "https" or parts.query:
        return False
    host = (parts.hostname or "").lower()
    return any(host == h and pattern.match(parts.path) for h, pattern in _FULL_LISTING_PATTERNS)


@dataclass(frozen=True)
class FreshnessOutcome:
    applied: bool
    confirmed: int = 0
    missed: int = 0
    closed: int = 0
    reopened: int = 0
    reason: str | None = None


def apply_listing_freshness(
    db: Session,
    *,
    source_id: UUID,
    run_id: UUID,
    run_status: str,
    connector_type: str,
    connector_configuration: dict | None,
    now: datetime | None = None,
) -> FreshnessOutcome:
    """Apply a completed run's listing to the source's jobs. Caller commits."""
    current = now or datetime.now(timezone.utc)
    if str(run_status).upper() != "SUCCEEDED":
        return FreshnessOutcome(False, reason="RUN_NOT_SUCCEEDED")
    if not is_full_listing_connector(connector_type, connector_configuration):
        return FreshnessOutcome(False, reason="LISTING_NOT_KNOWN_COMPLETE")

    seen_ids = {
        value for value in db.scalars(
            select(JobDiscoveryEvent.external_record_id).where(
                JobDiscoveryEvent.acquisition_run_id == run_id,
                JobDiscoveryEvent.source_id == source_id,
            )
        ) if value
    }
    if not seen_ids:
        # An empty listing is indistinguishable from an outage; never close on it.
        return FreshnessOutcome(False, reason="EMPTY_LISTING")

    rows = db.execute(
        select(JobProvenance.job_id, RawSourceRecord.external_record_id)
        .join(RawSourceRecord, RawSourceRecord.id == JobProvenance.raw_source_record_id)
        .where(JobProvenance.source_id == source_id, RawSourceRecord.source_id == source_id)
    ).all()
    ids_by_job: dict[UUID, set[str]] = {}
    for job_id, external_id in rows:
        ids_by_job.setdefault(job_id, set())
        if external_id:
            ids_by_job[job_id].add(external_id)
    if not ids_by_job:
        return FreshnessOutcome(True)

    other_source_jobs = set(db.scalars(
        select(JobProvenance.job_id).where(
            JobProvenance.source_id != source_id, JobProvenance.job_id.in_(list(ids_by_job))
        )
    ))
    confirmed = missed = closed = reopened = 0
    for job in db.scalars(select(Job).where(Job.id.in_(list(ids_by_job))).with_for_update()):
        listed = bool(ids_by_job[job.id] & seen_ids)
        meta = dict(job.metadata_json or {})
        if listed:
            was_closed_by_listing = (
                job.lifecycle_status == "CLOSED" and meta.get("closed_reason") == CLOSED_REASON
            )
            if job.lifecycle_status in TERMINAL_LIFECYCLE_STATES and not was_closed_by_listing:
                continue
            if was_closed_by_listing:
                reopened += 1
                meta.pop("closed_reason", None)
            job.lifecycle_status = "ACTIVE"
            job.freshness_status = "FRESH"
            job.last_verified_at = current
            job.last_observed_at = current
            meta["listing_misses"] = 0
            meta["listing_confirmed_at"] = current.isoformat()
            job.metadata_json = meta
            confirmed += 1
            continue
        if job.lifecycle_status in TERMINAL_LIFECYCLE_STATES:
            continue
        misses = int(meta.get("listing_misses") or 0) + 1
        meta["listing_misses"] = misses
        missed += 1
        if misses >= MISS_THRESHOLD and job.id not in other_source_jobs:
            job.lifecycle_status = "CLOSED"
            job.freshness_status = "STALE"
            meta["closed_reason"] = CLOSED_REASON
            meta["closed_at"] = current.isoformat()
            closed += 1
        else:
            job.freshness_status = "STALE"
        job.metadata_json = meta
    db.flush()
    return FreshnessOutcome(True, confirmed=confirmed, missed=missed, closed=closed, reopened=reopened)
