"""Durable creation and scheduling of candidate discovery missions."""
from __future__ import annotations

import re
from datetime import datetime, time as datetime_time, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.infrastructure.db.models import (
    CandidateDiscoverySettings,
    CandidateProfile,
    CareerPathway,
    DiscoveryMission,
    OutboxEvent,
    WorkItem,
)
from app.application.jobs.discovery_waves import DiscoveryPolicy


def enqueue_discovery_completion_notification(
    db: Session, mission: DiscoveryMission, completed_at: datetime
) -> None:
    """Persist one idempotent completion signal for a terminal discovery mission."""
    existing_id = db.scalar(
        select(OutboxEvent.id)
        .where(
            OutboxEvent.event_type == "DISCOVERY_MISSION_COMPLETED",
            OutboxEvent.aggregate_type == "DiscoveryMission",
            OutboxEvent.aggregate_id == mission.id,
        )
        .limit(1)
    )
    if existing_id is not None:
        return
    db.add(OutboxEvent(
        event_type="DISCOVERY_MISSION_COMPLETED",
        aggregate_type="DiscoveryMission",
        aggregate_id=mission.id,
        payload={"mission_id": str(mission.id)},
        status="PENDING",
        attempt_count=0,
        available_at=completed_at,
    ))


def validate_discovery_settings(
    *,
    status: str,
    cadence_minutes: int,
    target_opportunities: int,
    max_job_age_days: int,
    evaluation_depth: str,
    source_filters: dict,
    schedule_type: str = "INTERVAL",
    schedule_time: str = "09:00",
    schedule_timezone: str = "UTC",
    schedule_weekday: int | None = None,
) -> None:
    if status not in {"ENABLED", "DISABLED"}:
        raise ValueError("status must be ENABLED or DISABLED")
    if not isinstance(cadence_minutes, int) or isinstance(cadence_minutes, bool) or cadence_minutes < 15:
        raise ValueError("cadence_minutes must be an integer of at least 15")
    if not isinstance(target_opportunities, int) or isinstance(target_opportunities, bool) or target_opportunities < 1:
        raise ValueError("target_opportunities must be a positive integer")
    if not isinstance(max_job_age_days, int) or isinstance(max_job_age_days, bool) or max_job_age_days < 1:
        raise ValueError("max_job_age_days must be a positive integer")
    if evaluation_depth not in {"discovery_only", "preliminary", "full"}:
        raise ValueError("unsupported evaluation_depth")
    if not isinstance(source_filters, dict):
        raise ValueError("source_filters must be an object")
    # Candidate discovery preferences may select approved sources, but must
    # never override administrator-controlled connector runtime configuration.
    allowed_filter_keys = {"source_ids", "source_types", "connector_types"}
    unknown_keys = set(source_filters) - allowed_filter_keys
    if unknown_keys:
        raise ValueError("source_filters contains unsupported keys")
    for key in ("source_ids", "source_types", "connector_types"):
        value = source_filters.get(key)
        if value is not None and (
            not isinstance(value, list)
            or any(not isinstance(item, str) or not item.strip() for item in value)
        ):
            raise ValueError(f"source_filters.{key} must be a list of non-empty strings")
    if not isinstance(schedule_type, str) or schedule_type not in {"INTERVAL", "DAILY", "WEEKLY"}:
        raise ValueError("unsupported schedule_type")
    if not isinstance(schedule_time, str) or not re.fullmatch(
        r"(?:[01]\d|2[0-3]):[0-5]\d", schedule_time
    ):
        raise ValueError("schedule_time must use HH:MM in 24-hour time")
    if not isinstance(schedule_timezone, str) or not schedule_timezone.strip():
        raise ValueError("schedule_timezone must be a valid IANA timezone")
    try:
        ZoneInfo(schedule_timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("schedule_timezone must be a valid IANA timezone") from exc
    if schedule_type == "WEEKLY":
        if (
            not isinstance(schedule_weekday, int)
            or isinstance(schedule_weekday, bool)
            or not 0 <= schedule_weekday <= 6
        ):
            raise ValueError("schedule_weekday must be an integer from 0 (Monday) to 6 (Sunday)")
    elif schedule_weekday is not None:
        raise ValueError("schedule_weekday is only supported for WEEKLY schedules")


def next_calendar_schedule_at(
    *,
    schedule_type: str,
    schedule_time: str,
    schedule_timezone: str,
    schedule_weekday: int | None,
    after: datetime,
) -> datetime:
    """Return the next daily/weekly local schedule occurrence as a UTC timestamp."""
    if schedule_type not in {"DAILY", "WEEKLY"}:
        raise ValueError("calendar schedule must be DAILY or WEEKLY")
    zone = ZoneInfo(schedule_timezone)
    hour, minute = (int(part) for part in schedule_time.split(":"))
    after_utc = after.astimezone(timezone.utc)
    local_after = after_utc.astimezone(zone)
    candidate_date = local_after.date()

    if schedule_type == "WEEKLY":
        if schedule_weekday is None or not 0 <= schedule_weekday <= 6:
            raise ValueError("WEEKLY schedule requires schedule_weekday from 0 to 6")
        candidate_date += timedelta(days=(schedule_weekday - candidate_date.weekday()) % 7)

    def resolve_local(day):
        requested = datetime.combine(day, datetime_time(hour, minute), tzinfo=zone)
        # Normalize nonexistent local times across daylight-saving transitions.
        return requested.astimezone(timezone.utc).astimezone(zone)

    candidate = resolve_local(candidate_date)
    if candidate.astimezone(timezone.utc) <= after_utc:
        candidate_date += timedelta(days=1 if schedule_type == "DAILY" else 7)
        candidate = resolve_local(candidate_date)
    return candidate.astimezone(timezone.utc)


def initial_next_run_at(settings: CandidateDiscoverySettings, *, now: datetime) -> datetime:
    """Use immediate execution for legacy interval settings and calendar time for DAILY/WEEKLY."""
    schedule_type = getattr(settings, "schedule_type", "INTERVAL")
    if schedule_type == "INTERVAL":
        return now
    return next_calendar_schedule_at(
        schedule_type=schedule_type,
        schedule_time=getattr(settings, "schedule_time", "09:00"),
        schedule_timezone=getattr(settings, "schedule_timezone", "UTC"),
        schedule_weekday=getattr(settings, "schedule_weekday", None),
        after=now,
    )


def approved_pathway_snapshot(db: Session, candidate_profile_id: UUID) -> list[dict]:
    """Return a stable snapshot of only currently approved candidate pathways."""
    pathways = db.scalars(
        select(CareerPathway)
        .where(
            CareerPathway.candidate_profile_id == candidate_profile_id,
            CareerPathway.approval_status == "APPROVED",
        )
        .order_by(CareerPathway.id)
    ).all()
    return [
        {
            "id": str(pathway.id),
            "revision": pathway.revision,
            "title": pathway.title,
            "role_family": pathway.role_family,
            "target_roles": pathway.target_roles,
            "target_seniority": pathway.target_seniority,
            "domains": pathway.domains,
        }
        for pathway in pathways
    ]


def policy_snapshot(
    settings: CandidateDiscoverySettings | None,
    *,
    db: Session | None = None,
    candidate_profile_id: UUID | None = None,
) -> dict:
    if settings is None:
        policy = DiscoveryPolicy()
        snapshot = {
            "target_opportunities": policy.target_opportunities,
            "max_job_age_days": policy.max_job_age_days,
            "verification_required": policy.minimum_verification_required,
            "evaluation_depth": policy.evaluation_depth,
            "waves": [{"key": wave.key, "priority": wave.priority} for wave in policy.ordered_waves],
        }
    else:
        # Older candidate settings may contain acquisition_context from a previous
        # contract. Drop only that legacy field when snapshotting; reject it on new
        # writes and ignore it again in the worker as defense in depth.
        source_filters = settings.source_filters
        if isinstance(source_filters, dict) and "acquisition_context" in source_filters:
            source_filters = {
                key: value for key, value in source_filters.items()
                if key != "acquisition_context"
            }
        validate_discovery_settings(
            status=settings.status,
            cadence_minutes=settings.cadence_minutes,
            target_opportunities=settings.target_opportunities,
            max_job_age_days=settings.max_job_age_days,
            evaluation_depth=settings.evaluation_depth,
            source_filters=source_filters,
            schedule_type=getattr(settings, "schedule_type", "INTERVAL"),
            schedule_time=getattr(settings, "schedule_time", "09:00"),
            schedule_timezone=getattr(settings, "schedule_timezone", "UTC"),
            schedule_weekday=getattr(settings, "schedule_weekday", None),
        )
        policy = DiscoveryPolicy(
            target_opportunities=settings.target_opportunities,
            max_job_age_days=settings.max_job_age_days,
            minimum_verification_required=settings.verification_required,
            evaluation_depth=settings.evaluation_depth,
        )
        snapshot = {
            "target_opportunities": policy.target_opportunities,
            "max_job_age_days": policy.max_job_age_days,
            "verification_required": policy.minimum_verification_required,
            "evaluation_depth": policy.evaluation_depth,
            "source_filters": source_filters,
            "schedule": {
                "type": getattr(settings, "schedule_type", "INTERVAL"),
                "time": getattr(settings, "schedule_time", "09:00"),
                "timezone": getattr(settings, "schedule_timezone", "UTC"),
                "weekday": getattr(settings, "schedule_weekday", None),
            },
            "waves": [{"key": wave.key, "priority": wave.priority} for wave in policy.ordered_waves],
        }
    if db is not None and candidate_profile_id is not None:
        snapshot["approved_career_pathways"] = approved_pathway_snapshot(
            db, candidate_profile_id
        )
    return snapshot


def create_discovery_mission(
    db: Session,
    *,
    candidate_profile_id: UUID,
    idempotency_key: str,
    trigger_type: str = "MANUAL",
    settings_id: UUID | None = None,
    commit: bool = True,
) -> DiscoveryMission:
    """Create one durable mission and its work item, safely replayable by key."""
    if not idempotency_key or len(idempotency_key) > 255:
        raise ValueError("idempotency_key must contain 1–255 characters")
    if trigger_type not in {"MANUAL", "SCHEDULED", "RETRY"}:
        raise ValueError("unsupported trigger_type")
    candidate = db.get(CandidateProfile, candidate_profile_id)
    if candidate is None:
        raise ValueError("candidate profile does not exist")
    settings = db.get(CandidateDiscoverySettings, settings_id) if settings_id else None
    if settings_id and (settings is None or settings.candidate_profile_id != candidate_profile_id):
        raise ValueError("discovery settings do not belong to candidate")
    if trigger_type == "SCHEDULED" and (settings is None or settings.status != "ENABLED"):
        raise ValueError("scheduled mission requires enabled candidate settings")

    now = datetime.now(timezone.utc)
    mission_id = uuid4()
    statement = (
        insert(DiscoveryMission)
        .values(
            id=mission_id,
            candidate_profile_id=candidate_profile_id,
            settings_id=settings.id if settings else None,
            idempotency_key=idempotency_key,
            trigger_type=trigger_type,
            status="PENDING",
            policy_snapshot=policy_snapshot(settings, db=db, candidate_profile_id=candidate_profile_id),
            current_wave_key=None,
            wave_index=0,
            cumulative_usable_opportunities=0,
            started_at=None,
            completed_at=None,
            next_attempt_at=now,
            error_code=None,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_discovery_mission_candidate_key")
        .returning(DiscoveryMission.id)
    )
    created_id = db.scalar(statement)
    if created_id is None:
        mission = db.scalar(select(DiscoveryMission).where(
            DiscoveryMission.candidate_profile_id == candidate_profile_id,
            DiscoveryMission.idempotency_key == idempotency_key,
        ))
        if mission is None:
            raise RuntimeError("discovery mission could not be retrieved after idempotent insert")
        if commit:
            db.commit()
        return mission

    work_key = f"discovery-mission:{created_id}"
    work_statement = (
        insert(WorkItem)
        .values(
            id=uuid4(),
            execution_id=None,
            work_type="DISCOVERY_MISSION",
            resource_type="DiscoveryMission",
            resource_id=created_id,
            status="PENDING",
            idempotency_key=work_key,
            attempt_count=0,
            started_at=None,
            completed_at=None,
            error_code=None,
            error_details={},
            result=None,
            created_at=now,
            updated_at=now,
            revision=1,
        )
        .on_conflict_do_nothing(constraint="uq_work_items_type_idempotency")
    )
    db.execute(work_statement)
    if commit:
        db.commit()
    mission = db.get(DiscoveryMission, created_id)
    if mission is None:
        raise RuntimeError("created discovery mission could not be retrieved")
    return mission


def schedule_due_missions(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = 25,
) -> list[UUID]:
    """Claim due settings and enqueue at most one mission per due schedule slot."""
    if limit < 1:
        raise ValueError("limit must be positive")
    current = now or datetime.now(timezone.utc)
    due = db.scalars(
        select(CandidateDiscoverySettings)
        .where(
            CandidateDiscoverySettings.status == "ENABLED",
            CandidateDiscoverySettings.next_run_at.is_not(None),
            CandidateDiscoverySettings.next_run_at <= current,
        )
        .order_by(CandidateDiscoverySettings.next_run_at, CandidateDiscoverySettings.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()

    mission_ids: list[UUID] = []
    for settings in due:
        scheduled_slot = settings.next_run_at
        if scheduled_slot is None:
            continue
        key = "scheduled:" + scheduled_slot.astimezone(timezone.utc).isoformat()
        mission = create_discovery_mission(
            db,
            candidate_profile_id=settings.candidate_profile_id,
            idempotency_key=key,
            trigger_type="SCHEDULED",
            settings_id=settings.id,
            commit=False,
        )
        mission_ids.append(mission.id)
        settings.last_scheduled_at = current
        schedule_type = getattr(settings, "schedule_type", "INTERVAL")
        if schedule_type == "INTERVAL":
            next_run = scheduled_slot + timedelta(minutes=settings.cadence_minutes)
            while next_run <= current:
                next_run += timedelta(minutes=settings.cadence_minutes)
        else:
            next_run = next_calendar_schedule_at(
                schedule_type=schedule_type,
                schedule_time=getattr(settings, "schedule_time", "09:00"),
                schedule_timezone=getattr(settings, "schedule_timezone", "UTC"),
                schedule_weekday=getattr(settings, "schedule_weekday", None),
                after=current,
            )
        settings.next_run_at = next_run
    db.commit()
    return mission_ids
