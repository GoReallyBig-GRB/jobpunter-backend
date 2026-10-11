"""Execute durable discovery missions, canonicalize results, and match jobs to candidates.

Only fresh, verified, complete postings with an actionable application route and
an APPLY evaluation count toward a candidate's discovery target.
"""
from __future__ import annotations

import argparse
import logging
import re
import signal
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.application.jobs.registered_acquisition import RegisteredAcquisitionService
from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry
from app.application.opportunities.evaluation_service import CandidateEvaluationService
from app.infrastructure.db.models import (
    AcquisitionRun,
    CandidateEvaluation,
    CandidatePreferences,
    CandidateProfile,
    DiscoveryMission,
    DiscoveryMissionSourceRun,
    Job,
    JobDescriptionVersion,
    JobResolution,
    OutboxEvent,
    RawSourceRecord,
    Source,
    SourceConnector,
    WorkItem,
)
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
from app.infrastructure.db.session import SessionLocal

logger = logging.getLogger("jobpunter.discovery_mission_worker")

WAVE_CONNECTORS: dict[str, set[str]] = {
    "official_feeds": {"API", "FEED", "RSS", "GREENHOUSE_API", "LEVER_API"},
    "jobspy": {"JOBSPY"},
    "targeted_scrapers": {"SCRAPY"},
    "browser_automation": {"PLAYWRIGHT"},
}
TERMINAL_OUTBOX_STATES = {"PROCESSED", "FAILED", "DEAD"}


def _source_matches_filters(source: Source, connector: SourceConnector, filters: dict) -> bool:
    """Apply only explicit allow-filters; omitted filters leave the configured source eligible."""
    source_ids = filters.get("source_ids")
    source_types = filters.get("source_types")
    connector_types = filters.get("connector_types")
    for key, value in (("source_ids", source_ids), ("source_types", source_types), ("connector_types", connector_types)):
        if value is not None and (not isinstance(value, list) or any(not isinstance(item, str) for item in value)):
            raise ValueError(f"source_filters.{key} must be a list of strings")
    if source_ids is not None and str(source.id) not in set(source_ids):
        return False
    if source_types is not None and str(source.source_type).upper() not in {
        value.upper() for value in source_types
    }:
        return False
    if connector_types is not None and str(connector.connector_type).upper() not in {
        value.upper() for value in connector_types
    }:
        return False
    return True


def _eligible_sources(db: Session, wave_key: str, filters: dict) -> list[tuple[Source, SourceConnector]]:
    connector_types = WAVE_CONNECTORS.get(wave_key, set())
    if not connector_types:
        return []
    rows = db.execute(
        select(Source, SourceConnector)
        .join(SourceConnector, SourceConnector.source_id == Source.id)
        .where(
            Source.status == "ACTIVE",
            SourceConnector.status == "ACTIVE",
            SourceConnector.connector_type.in_(sorted(connector_types)),
        )
        .order_by(Source.name, Source.id, SourceConnector.connector_type)
    ).all()
    return [
        (source, connector)
        for source, connector in rows
        if _source_matches_filters(source, connector, filters)
    ]


def _raw_records_resolved(db: Session, acquisition_run_ids: list[UUID]) -> bool:
    if not acquisition_run_ids:
        return True
    raw_ids = list(db.scalars(
        select(RawSourceRecord.id).where(
            RawSourceRecord.acquisition_run_id.in_(acquisition_run_ids)
        )
    ).all())
    if not raw_ids:
        return True
    resolution_ids = set(db.scalars(
        select(JobResolution.raw_source_record_id).where(
            JobResolution.raw_source_record_id.in_(raw_ids)
        )
    ).all())
    unresolved_ids = set(raw_ids) - resolution_ids
    if not unresolved_ids:
        return True
    events = db.scalars(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_type == "RawSourceRecord",
            OutboxEvent.aggregate_id.in_(unresolved_ids),
        )
    ).all()
    terminal_by_raw = {
        event.aggregate_id
        for event in events
        if event.status in TERMINAL_OUTBOX_STATES
    }
    return unresolved_ids.issubset(terminal_by_raw)


def _canonicalized_count(db: Session, acquisition_run_id: UUID | None) -> int:
    if acquisition_run_id is None:
        return 0
    return len(set(db.scalars(
        select(JobResolution.job_id).join(
            RawSourceRecord, RawSourceRecord.id == JobResolution.raw_source_record_id
        ).where(
            RawSourceRecord.acquisition_run_id == acquisition_run_id,
            JobResolution.job_id.is_not(None),
            JobResolution.status.in_({"CREATED", "MATCHED"}),
        )
    ).all()))


def _enqueue_again(db: Session, work_item: WorkItem, *, delay_seconds: int = 0) -> None:
    work_item.status = "PENDING"
    work_item.started_at = None
    work_item.completed_at = None
    work_item.error_code = None
    work_item.error_details = {}
    work_item.updated_at = datetime.now(timezone.utc)
    # WorkItem has no availability column yet. Workers should poll with a bounded
    # interval; next_attempt_at on the mission is the authoritative due time.
    if delay_seconds:
        work_item.result = {"not_before": (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()}


def _evaluate_mission_opportunities(db: Session, mission: DiscoveryMission) -> tuple[int, dict[UUID, int], int]:
    """Evaluate canonical jobs against this candidate and count only actionable matches.

    Raw records, incomplete/stale postings, closed jobs, unverified postings when
    verification is required, and evaluations other than APPLY never count toward
    the discovery target. Existing evaluations are reused when their input
    revisions still match, so a recovered mission does not create duplicate
    evaluation versions for unchanged inputs.
    """
    profile = db.get(CandidateProfile, mission.candidate_profile_id)
    if profile is None:
        raise RuntimeError("discovery candidate profile is missing")

    preferences_row = db.scalar(select(CandidatePreferences).where(
        CandidatePreferences.candidate_profile_id == profile.id
    ))
    preferences = preferences_row.preferences if preferences_row else {}
    target_roles = preferences.get("targetRoles", preferences.get("target_roles", []))
    if not isinstance(target_roles, list):
        raise ValueError("candidate target roles must be a list")
    role_keys = sorted({
        "-".join(part for part in re.split(r"[^a-z0-9]+", role.strip().casefold()) if part)
        for role in target_roles
        if isinstance(role, str) and role.strip()
    })
    if not role_keys:
        return 0, {}, 0

    source_runs = list(db.scalars(select(DiscoveryMissionSourceRun).where(
        DiscoveryMissionSourceRun.mission_id == mission.id,
        DiscoveryMissionSourceRun.acquisition_run_id.is_not(None),
    )).all())
    acquisition_run_ids = [row.acquisition_run_id for row in source_runs if row.acquisition_run_id]
    if not acquisition_run_ids:
        return 0, {}, 0

    job_ids = list(db.scalars(
        select(JobResolution.job_id).join(
            RawSourceRecord, RawSourceRecord.id == JobResolution.raw_source_record_id
        ).where(
            RawSourceRecord.acquisition_run_id.in_(acquisition_run_ids),
            JobResolution.job_id.is_not(None),
            JobResolution.status.in_({"CREATED", "MATCHED"}),
        ).distinct()
    ).all())

    now = datetime.now(timezone.utc)
    max_age_days = int((mission.policy_snapshot or {}).get("max_job_age_days", 21))
    cutoff = now - timedelta(days=max_age_days)
    verification_required = bool((mission.policy_snapshot or {}).get("verification_required", True))
    usable_job_ids: set[UUID] = set()
    evaluated_count = 0

    for job_id in job_ids:
        job = db.get(Job, job_id)
        if job is None:
            continue
        if str(job.lifecycle_status).upper() in {"CLOSED", "EXPIRED", "ARCHIVED", "REMOVED"}:
            continue
        if job.last_observed_at is None or job.last_observed_at < cutoff:
            continue
        if verification_required and (job.last_verified_at is None or job.last_verified_at < cutoff):
            continue
        description = db.scalar(
            select(JobDescriptionVersion).where(
                JobDescriptionVersion.job_id == job.id
            ).order_by(JobDescriptionVersion.version_number.desc()).limit(1)
        )
        if (
            description is None
            or len((description.content or "").strip()) < 80
            or str(description.completeness_status).upper() in {
                "INCOMPLETE", "MISSING", "UNKNOWN", "PARTIAL"
            }
        ):
            continue
        if not isinstance(job.application_url, str) or not job.application_url.strip():
            continue

        matched_for_job = False
        for role_key in role_keys:
            latest = db.scalar(
                select(CandidateEvaluation).where(
                    CandidateEvaluation.candidate_profile_id == profile.id,
                    CandidateEvaluation.job_id == job.id,
                    CandidateEvaluation.target_role_key == role_key,
                ).order_by(CandidateEvaluation.evaluation_version.desc()).limit(1)
            )
            current_snapshot = {
                "candidate_profile_revision": profile.revision,
                "preferences_revision": preferences_row.revision if preferences_row else 0,
                "job_revision": job.revision,
                "job_description_version_id": str(description.id),
            }
            reusable = latest is not None and latest.validity_status == "CURRENT" and all(
                (latest.input_snapshot or {}).get(key) == value
                for key, value in current_snapshot.items()
            )
            evaluation = latest if reusable else CandidateEvaluationService(db).evaluate(
                profile.user_id, job.id, role_key
            )
            evaluated_count += 0 if reusable else 1
            if evaluation.outcome == "APPLY":
                matched_for_job = True
        if matched_for_job:
            usable_job_ids.add(job.id)

    per_run: dict[UUID, int] = {}
    for source_run in source_runs:
        if source_run.acquisition_run_id is None:
            continue
        run_job_ids = set(db.scalars(
            select(JobResolution.job_id).join(
                RawSourceRecord, RawSourceRecord.id == JobResolution.raw_source_record_id
            ).where(
                RawSourceRecord.acquisition_run_id == source_run.acquisition_run_id,
                JobResolution.job_id.is_not(None),
                JobResolution.status.in_({"CREATED", "MATCHED"}),
            ).distinct()
        ).all())
        per_run[source_run.id] = len(run_job_ids & usable_job_ids)
        source_run.usable_count = per_run[source_run.id]

    return len(usable_job_ids), per_run, evaluated_count


def process_mission_step(db: Session, mission_id: UUID) -> str:
    """Advance one mission by at most one wave; return its persisted mission status."""
    mission = db.scalar(
        select(DiscoveryMission).where(DiscoveryMission.id == mission_id).with_for_update()
    )
    if mission is None:
        return "MISSING"
    work_item = db.scalar(
        select(WorkItem).where(
            WorkItem.work_type == "DISCOVERY_MISSION",
            WorkItem.resource_id == mission.id,
        ).with_for_update()
    )
    if work_item is None:
        mission.status = "FAILED"
        mission.error_code = "WORK_ITEM_MISSING"
        mission.completed_at = datetime.now(timezone.utc)
        db.commit()
        return mission.status
    if mission.status in {"TARGET_MET", "EXHAUSTED", "FAILED", "CANCELLED"}:
        work_item.status = "COMPLETED"
        work_item.completed_at = datetime.now(timezone.utc)
        db.commit()
        return mission.status

    now = datetime.now(timezone.utc)
    mission.started_at = mission.started_at or now
    mission.status = "RUNNING"
    policy = mission.policy_snapshot or {}
    waves = sorted(
        (wave for wave in policy.get("waves", []) if isinstance(wave, dict) and wave.get("key")),
        key=lambda wave: int(wave.get("priority", 0)),
    )
    wave_index = int(mission.wave_index or 0)

    # A previous wave may be waiting for the outbox/canonicalization pipeline.
    existing_wave_runs = list(db.scalars(
        select(DiscoveryMissionSourceRun).where(
            DiscoveryMissionSourceRun.mission_id == mission.id,
            DiscoveryMissionSourceRun.wave_key == mission.current_wave_key,
        )
    ).all()) if mission.current_wave_key else []
    pending_run_ids = [
        row.acquisition_run_id for row in existing_wave_runs
        if row.acquisition_run_id is not None
    ]
    if mission.status == "RUNNING" and mission.current_wave_key and existing_wave_runs:
        if not _raw_records_resolved(db, pending_run_ids):
            mission.status = "WAITING_FOR_CANONICALIZATION"
            mission.next_attempt_at = now + timedelta(seconds=15)
            _enqueue_again(db, work_item, delay_seconds=15)
            db.commit()
            return mission.status
        for row in existing_wave_runs:
            row.canonicalized_count = _canonicalized_count(db, row.acquisition_run_id)
            if row.status in {"SUCCEEDED", "SUCCEEDED_EMPTY", "PARTIAL"}:
                row.status = "CANONICALIZED"

        # Evaluate each completed wave before advancing. Once the candidate's
        # target is met, do not spend time or crawl budget on broader waves.
        usable_count, _per_run, evaluated_count = _evaluate_mission_opportunities(db, mission)
        mission.cumulative_usable_opportunities = usable_count
        target = int(policy.get("target_opportunities", 20))
        if usable_count >= target:
            mission.status = "TARGET_MET"
            mission.error_code = None
            mission.completed_at = now
            mission.next_attempt_at = None
            work_item.status = "COMPLETED"
            work_item.completed_at = now
            work_item.error_code = None
            work_item.error_details = {}
            work_item.result = {
                "mission_status": mission.status,
                "usable_opportunities": usable_count,
                "target_opportunities": target,
                "evaluated_jobs": evaluated_count,
                "evaluation_required": False,
            }
            db.commit()
            return mission.status

        mission.wave_index += 1
        mission.current_wave_key = None
        mission.status = "PENDING"
        mission.next_attempt_at = now
        _enqueue_again(db, work_item)
        db.commit()
        return mission.status

    if wave_index >= len(waves):
        usable_count, _per_run, evaluated_count = _evaluate_mission_opportunities(db, mission)
        mission.cumulative_usable_opportunities = usable_count
        target = int(policy.get("target_opportunities", 20))
        mission.status = "TARGET_MET" if usable_count >= target else "EXHAUSTED"
        mission.error_code = None
        mission.completed_at = now
        mission.next_attempt_at = None
        work_item.status = "COMPLETED"
        work_item.completed_at = now
        work_item.error_code = None
        work_item.error_details = {}
        work_item.result = {
            "mission_status": mission.status,
            "usable_opportunities": usable_count,
            "target_opportunities": target,
            "evaluated_jobs": evaluated_count,
            "evaluation_required": False,
        }
        db.commit()
        return mission.status

    wave = waves[wave_index]
    wave_key = str(wave["key"])
    mission.current_wave_key = wave_key
    mission.wave_index = wave_index
    filters = policy.get("source_filters") or {}
    if not isinstance(filters, dict):
        mission.status = "FAILED"
        mission.error_code = "INVALID_SOURCE_FILTERS"
        mission.completed_at = now
        work_item.status = "FAILED"
        work_item.error_code = mission.error_code
        work_item.completed_at = now
        db.commit()
        return mission.status

    sources = _eligible_sources(db, wave_key, filters)
    mission.status = "RUNNING"
    db.commit()  # Persist mission intent before invoking connectors.

    for source, connector in sources:
        prior_attempt = db.scalar(
            select(DiscoveryMissionSourceRun.attempt_number)
            .where(
                DiscoveryMissionSourceRun.mission_id == mission.id,
                DiscoveryMissionSourceRun.source_id == source.id,
                DiscoveryMissionSourceRun.wave_key == wave_key,
            )
            .order_by(DiscoveryMissionSourceRun.attempt_number.desc())
            .limit(1)
        )
        attempt = int(prior_attempt or 0) + 1
        run_key = f"mission:{mission.id}:wave:{wave_key}:source:{source.id}:attempt:{attempt}"
        source_run = DiscoveryMissionSourceRun(
            id=uuid4(),
            mission_id=mission.id,
            source_id=source.id,
            source_connector_id=connector.id,
            wave_key=wave_key,
            attempt_number=attempt,
            run_key=run_key,
            status="RUNNING",
            acquired_count=0,
            persisted_count=0,
            canonicalized_count=0,
            usable_count=0,
            error_code=None,
            error_details={},
            started_at=now,
            completed_at=None,
            created_at=now,
        )
        db.add(source_run)
        db.commit()
        try:
            context_override = filters.get("acquisition_context", {})
            if not isinstance(context_override, dict):
                raise ValueError("source_filters.acquisition_context must be an object")
            result = RegisteredAcquisitionService(
                SqlAlchemyAcquisitionRepository(db), AcquisitionConnectorRegistry()
            ).run(source.id, connector.id, run_key, context_override)
            source_run.acquisition_run_id = result.run_id
            source_run.acquired_count = result.acquired_count
            source_run.persisted_count = result.persisted_count
            source_run.status = "SUCCEEDED_EMPTY" if result.acquired_count == 0 else "SUCCEEDED"
            source_run.completed_at = datetime.now(timezone.utc)
            db.commit()
        except Exception as exc:
            db.rollback()
            failed_row = db.scalar(select(DiscoveryMissionSourceRun).where(
                DiscoveryMissionSourceRun.mission_id == mission.id,
                DiscoveryMissionSourceRun.run_key == run_key,
            ))
            if failed_row is not None:
                failed_row.status = "FAILED"
                failed_row.error_code = type(exc).__name__
                failed_row.error_details = {"error_type": type(exc).__name__}
                failed_row.completed_at = datetime.now(timezone.utc)
                db.commit()
            logger.warning(
                "discovery source failed mission=%s source=%s connector=%s error=%s",
                mission.id, source.id, connector.connector_type, type(exc).__name__,
            )

    # Record a completed wave even when no sources matched; failures are visible
    # in source-run history and do not masquerade as successful empty searches.
    wave_runs = list(db.scalars(select(DiscoveryMissionSourceRun).where(
        DiscoveryMissionSourceRun.mission_id == mission.id,
        DiscoveryMissionSourceRun.wave_key == wave_key,
    )).all())
    if any(row.persisted_count > 0 for row in wave_runs):
        mission.status = "WAITING_FOR_CANONICALIZATION"
        mission.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=15)
        _enqueue_again(db, work_item, delay_seconds=15)
    else:
        mission.wave_index = wave_index + 1
        mission.current_wave_key = None
        mission.status = "PENDING"
        mission.next_attempt_at = datetime.now(timezone.utc)
        _enqueue_again(db, work_item)
    db.commit()
    return mission.status


class DiscoveryMissionWorker:
    """PostgreSQL-backed work-item consumer with row locking and stale-claim recovery."""

    def __init__(self, session_factory=SessionLocal, *, lease_seconds: int = 300):
        if lease_seconds < 30:
            raise ValueError("lease_seconds must be at least 30")
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds

    def claim_batch(self, limit: int = 10) -> list[UUID]:
        if limit < 1:
            raise ValueError("limit must be positive")
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=self.lease_seconds)
        with self.session_factory() as db:
            items = db.scalars(
                select(WorkItem)
                .outerjoin(DiscoveryMission, WorkItem.resource_id == DiscoveryMission.id)
                .where(
                    WorkItem.work_type == "DISCOVERY_MISSION",
                    or_(
                        and_(
                            WorkItem.status == "PENDING",
                            or_(DiscoveryMission.next_attempt_at.is_(None), DiscoveryMission.next_attempt_at <= now),
                        ),
                        and_(
                            WorkItem.status == "PROCESSING",
                            or_(WorkItem.started_at.is_(None), WorkItem.started_at <= stale_before),
                            or_(DiscoveryMission.next_attempt_at.is_(None), DiscoveryMission.next_attempt_at <= now),
                        ),
                    ),
                ).order_by(WorkItem.created_at, WorkItem.id)
                .limit(limit).with_for_update(skip_locked=True, of=WorkItem)
            ).all()
            mission_ids = []
            for item in items:
                item.status = "PROCESSING"
                item.started_at = now
                item.attempt_count += 1
                item.updated_at = now
                if item.resource_id is not None:
                    mission_ids.append(item.resource_id)
            db.commit()
            return mission_ids

    def run_once(self, limit: int = 10) -> dict[str, int]:
        ids = self.claim_batch(limit)
        result = {"claimed": len(ids), "advanced": 0, "failed": 0}
        for mission_id in ids:
            try:
                with self.session_factory() as db:
                    status = process_mission_step(db, mission_id)
                if status == "FAILED":
                    result["failed"] += 1
                else:
                    result["advanced"] += 1
            except Exception as exc:
                logger.exception(
                    "discovery mission step failed mission=%s error=%s",
                    mission_id, type(exc).__name__,
                )
                with self.session_factory() as db:
                    mission = db.get(DiscoveryMission, mission_id)
                    work_item = db.scalar(select(WorkItem).where(
                        WorkItem.work_type == "DISCOVERY_MISSION",
                        WorkItem.resource_id == mission_id,
                    ))
                    if mission is not None:
                        mission.status = "RETRY_WAIT"
                        mission.error_code = type(exc).__name__
                        mission.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=30)
                    if work_item is not None:
                        _enqueue_again(db, work_item, delay_seconds=30)
                    db.commit()
                result["failed"] += 1
        return result


def run_worker(
    *, interval_seconds: float = 2.0, batch_size: int = 10, once: bool = False,
    should_stop: Callable[[], bool] = lambda: False, sleep: Callable[[float], None] = time.sleep,
    worker: DiscoveryMissionWorker | None = None,
) -> int:
    if interval_seconds <= 0 or batch_size < 1:
        raise ValueError("interval_seconds and batch_size must be positive")
    consumer = worker or DiscoveryMissionWorker()
    cycles = 0
    while not should_stop():
        outcome = consumer.run_once(batch_size)
        cycles += 1
        logger.info("discovery mission cycle=%s outcome=%s", cycles, outcome)
        if once:
            break
        if outcome["claimed"] == 0:
            sleep(interval_seconds)
    return cycles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stopped = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    run_worker(
        interval_seconds=args.interval_seconds,
        batch_size=args.batch_size,
        once=args.once,
        should_stop=lambda: stopped,
    )


if __name__ == "__main__":
    main()
