"""Cheap "may this worker have work?" probes used by the worker host to avoid idle process spawns.

Each probe is ONE indexed EXISTS query and is deliberately a SUPERSET of the worker's own claim
rules (it may say yes when the worker then finds nothing, never the reverse by design). The worker's
own claim query stays authoritative; a probe only decides whether to pay for starting a process.
Safety nets: any probe error answers True (fail open), and the host starts every worker at least
once per ``safety_net_seconds`` regardless of the probe, so a wrong probe delays work, not strands it.
Workers without an entry here (job_verification: also runs the staleness sweep) rely on that interval.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.application.jobs.source_refresh import WORK_TYPE as SOURCE_REFRESH_WORK_TYPE
from app.infrastructure.db.models import (
    CandidateDiscoverySettings,
    DiscoveryMission,
    DiscoveryRun,
    DiscoverySchedule,
    JobDescriptionVersion,
    OutboxEvent,
    Source,
    SourceDiscoveryCandidate,
    SourceRefreshSchedule,
    WorkItem,
)


def _work_item_open(work_type: str, now: datetime, stale_seconds: int) -> object:
    stale_before = now - timedelta(seconds=stale_seconds)
    return exists().where(
        WorkItem.work_type == work_type,
        or_(
            WorkItem.status == "PENDING",
            and_(
                WorkItem.status == "PROCESSING",
                or_(WorkItem.started_at.is_(None), WorkItem.started_at <= stale_before),
            ),
        ),
    )


def _outbox(now: datetime) -> object:
    return exists().where(OutboxEvent.status == "PENDING", OutboxEvent.available_at <= now)


def _scheduler(now: datetime) -> object:
    return or_(
        exists().where(
            CandidateDiscoverySettings.status == "ENABLED",
            CandidateDiscoverySettings.next_run_at.is_not(None),
            CandidateDiscoverySettings.next_run_at <= now,
        ),
        exists().where(DiscoverySchedule.status == "ACTIVE", DiscoverySchedule.next_run_at <= now),
        exists().where(
            SourceRefreshSchedule.status == "ACTIVE",
            SourceRefreshSchedule.next_run_at <= now,
            SourceRefreshSchedule.source_id.in_(select(Source.id).where(Source.status == "ACTIVE")),
        ),
    )


def _mission(now: datetime) -> object:
    return _work_item_open("DISCOVERY_MISSION", now, 300)


def _run(now: datetime) -> object:
    return exists().where(
        WorkItem.work_type == "DISCOVERY_RUN",
        WorkItem.resource_id == DiscoveryRun.id,
        DiscoveryRun.status.in_(("PENDING", "RUNNING")),
        or_(DiscoveryRun.next_attempt_at.is_(None), DiscoveryRun.next_attempt_at <= now),
        or_(
            WorkItem.status == "PENDING",
            and_(
                WorkItem.status == "PROCESSING",
                or_(DiscoveryRun.lease_expires_at.is_(None), DiscoveryRun.lease_expires_at <= now),
            ),
        ),
    )


def _refresh(now: datetime) -> object:
    stale_before = now - timedelta(seconds=900)
    return exists().where(
        WorkItem.work_type == SOURCE_REFRESH_WORK_TYPE,
        or_(
            and_(
                WorkItem.status == "PENDING",
                func.coalesce(WorkItem.result["not_before"].as_string(), "") <= now.isoformat(),
            ),
            and_(
                WorkItem.status == "PROCESSING",
                or_(WorkItem.started_at.is_(None), WorkItem.started_at <= stale_before),
            ),
        ),
    )


def _candidate(now: datetime) -> object:
    return exists().where(
        SourceDiscoveryCandidate.source_id.is_not(None),
        or_(
            SourceDiscoveryCandidate.status == "APPROVED",
            and_(
                SourceDiscoveryCandidate.status == "PROCESSING",
                SourceDiscoveryCandidate.lease_expires_at <= now,
            ),
        ),
        or_(
            SourceDiscoveryCandidate.next_attempt_at.is_(None),
            SourceDiscoveryCandidate.next_attempt_at <= now,
        ),
    )


def _evaluation(now: datetime) -> object:
    stale_before = now - timedelta(minutes=5)
    return exists().where(or_(
        DiscoveryMission.status == "AWAITING_EVALUATION",
        and_(DiscoveryMission.status == "EVALUATING", DiscoveryMission.updated_at <= stale_before),
    ))


def _extraction(now: datetime) -> object:
    # Superset: any USABLE description not yet extracted, failed (retry time ignored) or in progress.
    return exists().where(
        JobDescriptionVersion.completeness_status == "USABLE",
        JobDescriptionVersion.requirement_extraction_status.in_(("PENDING", "FAILED", "IN_PROGRESS")),
    )


_BUILDERS: dict[str, Callable[[datetime], object]] = {
    "outbox": _outbox,
    "discovery_scheduler": _scheduler,
    "discovery_mission_worker": _mission,
    "discovery_run_worker": _run,
    "source_refresh_worker": _refresh,
    "source_discovery_candidate_worker": _candidate,
    "discovery_evaluation_worker": _evaluation,
    "job_requirement_extraction_worker": _extraction,
}

PROBED_WORKERS: frozenset[str] = frozenset(_BUILDERS)


def work_may_exist(db: Session, name: str, now: datetime | None = None) -> bool:
    """True when ``name`` may have work. Workers without a probe answer False: they run only on
    the host's safety-net interval (job_verification: a full ``jobs`` scan every poll is costlier
    than the delay it would save)."""
    builder = _BUILDERS.get(name)
    if builder is None:
        return False
    return bool(db.scalar(select(builder(now or datetime.now(UTC)))))


def db_work_probe(name: str) -> bool:
    """Host-facing probe: one short session per call; any error answers True (fail open)."""
    from app.infrastructure.db.session import SessionLocal

    if name not in _BUILDERS:
        return False
    with SessionLocal() as db:
        return work_may_exist(db, name)
