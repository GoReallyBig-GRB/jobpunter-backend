"""Sync one candidate's own pool from the central job pool.

Deterministic and database-only: no connector, network or AI call is made here. A job enters the
candidate's pool when it is visible to the candidate, still open and recently observed, matches a
target role from the candidate's approved career pathways, and fits any stated location
preference. Entries whose central job has closed or gone stale are marked CLOSED, never deleted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.application.jobs.access import candidate_job_visibility_predicate
from app.infrastructure.db.models import CandidatePoolEntry, CandidateProfile, Job

CLOSED_LIFECYCLE = ("CLOSED", "EXPIRED", "ARCHIVED", "REMOVED")
MAX_NEW_ENTRIES_PER_SYNC = 200
MAX_TOKENS_PER_ROLE = 6
_TOKEN = re.compile(r"[a-z0-9+#.]+")
_REMOTE_MODES = ("REMOTE", "FULLY_REMOTE", "REMOTE_FIRST")


@dataclass(frozen=True)
class SyncResult:
    matched_total: int
    new_entries: int
    refreshed_entries: int
    closed_entries: int
    capped: bool


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def role_tokens(role: str) -> list[str]:
    tokens = _TOKEN.findall(role.casefold())
    seen: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.append(token)
    return seen[:MAX_TOKENS_PER_ROLE]


def _role_predicate(role: str):
    """Title contains the whole role phrase, or every significant word of it."""
    phrase = role.strip()
    title = func.coalesce(Job.canonical_title, "")
    clauses = [title.ilike(f"%{_like_escape(phrase)}%", escape="\\")]
    tokens = role_tokens(phrase)
    if len(tokens) > 1:
        clauses.append(and_(*[title.ilike(f"%{_like_escape(t)}%", escape="\\") for t in tokens]))
    return or_(*clauses)


def _location_predicate(preferences: dict):
    """Keep remote and location-unknown jobs; otherwise require a stated place to match."""
    places = [
        value.strip() for key in ("preferredLocations", "eligibleCountries")
        for value in (preferences.get(key) or []) if isinstance(value, str) and value.strip()
    ]
    if not places:
        return None
    clauses = [
        func.upper(func.coalesce(Job.work_mode, "")).in_(_REMOTE_MODES),
        and_(
            func.coalesce(Job.location_country, "") == "",
            func.coalesce(Job.location_city, "") == "",
            func.coalesce(Job.original_location_text, "") == "",
        ),
    ]
    for place in places[:20]:
        pattern = f"%{_like_escape(place)}%"
        for column in (Job.location_country, Job.location_city, Job.location_region, Job.original_location_text):
            clauses.append(func.coalesce(column, "").ilike(pattern, escape="\\"))
    return or_(*clauses)


def _is_open(job: Job, cutoff: datetime) -> bool:
    return (
        str(job.lifecycle_status).upper() not in CLOSED_LIFECYCLE
        and str(job.freshness_status).upper() != "STALE"
        and job.last_observed_at is not None
        and job.last_observed_at >= cutoff
    )


def sync_candidate_pool(
    db: Session,
    *,
    candidate_profile_id: UUID,
    target_roles: list[str],
    preferences: dict | None = None,
    max_job_age_days: int = 21,
    now: datetime | None = None,
    max_new_entries: int = MAX_NEW_ENTRIES_PER_SYNC,
) -> SyncResult:
    """Add newly matching jobs, refresh existing entries and close those no longer open. Commits."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_job_age_days)
    profile = db.get(CandidateProfile, candidate_profile_id)
    if profile is None:
        raise ValueError("candidate profile does not exist")
    roles = [r.strip() for r in target_roles if isinstance(r, str) and r.strip()]

    # 1. Close or refresh what the candidate already has.
    closed = refreshed = 0
    existing_rows = db.execute(
        select(CandidatePoolEntry, Job).join(Job, Job.id == CandidatePoolEntry.job_id)
        .where(CandidatePoolEntry.candidate_profile_id == candidate_profile_id)
    ).all()
    for entry, job in existing_rows:
        open_now = _is_open(job, cutoff)
        if entry.status == "ACTIVE" and not open_now:
            entry.status, entry.closed_at = "CLOSED", now
            closed += 1
        elif entry.status == "CLOSED" and open_now:
            entry.status, entry.closed_at = "ACTIVE", None
            refreshed += 1
        elif entry.status == "ACTIVE":
            refreshed += 1
        entry.last_synced_at = now
    known_job_ids = {entry.job_id for entry, _job in existing_rows}

    # 2. Add newly matching jobs from the central pool (no roles means nothing can match).
    new_entries = matched_total = 0
    capped = False
    if roles:
        filters = [
            Job.identity_status == "CANONICAL",
            candidate_job_visibility_predicate(profile.user_id),
            func.upper(Job.lifecycle_status).notin_(CLOSED_LIFECYCLE),
            func.upper(Job.freshness_status) != "STALE",
            Job.last_observed_at >= cutoff,
            or_(*[_role_predicate(role) for role in roles]),
        ]
        location = _location_predicate(preferences or {})
        if location is not None:
            filters.append(location)
        matched_total = int(db.scalar(select(func.count()).select_from(select(Job.id).where(*filters).subquery())) or 0)
        candidates = db.scalars(
            select(Job).where(*filters).order_by(Job.last_observed_at.desc(), Job.id).limit(
                max_new_entries + len(known_job_ids) + 1  # one extra row reveals whether the cap was hit
            )
        ).all()
        for job in candidates:
            if job.id in known_job_ids:
                continue
            if new_entries >= max_new_entries:
                capped = True
                break
            title = (job.canonical_title or "").casefold()
            basis_roles = [r for r in roles if r.casefold() in title or all(t in title for t in role_tokens(r))]
            db.add(CandidatePoolEntry(
                candidate_profile_id=candidate_profile_id, job_id=job.id, status="ACTIVE",
                match_basis={"roles": basis_roles[:5], "rule": "title-role-v1"},
                matched_at=now, last_synced_at=now,
            ))
            new_entries += 1
    db.commit()
    return SyncResult(matched_total, new_entries, refreshed, closed, capped)
