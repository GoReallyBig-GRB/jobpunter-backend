"""Durable creation and scheduling of candidate discovery missions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.infrastructure.db.models import (
    CandidateDiscoverySettings,
    CandidateProfile,
    DiscoveryMission,
    WorkItem,
)
from app.application.jobs.discovery_waves import DiscoveryPolicy


def validate_discovery_settings(
    *,
    status: str,
    cadence_minutes: int,
    target_opportunities: int,
    max_job_age_days: int,
    evaluation_depth: str,
    source_filters: dict,
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


def policy_snapshot(settings: CandidateDiscoverySettings | None) -> dict:
    if settings is None:
        policy = DiscoveryPolicy()
        return {
            "target_opportunities": policy.target_opportunities,
            "max_job_age_days": policy.max_job_age_days,
            "verification_required": policy.minimum_verification_required,
            "evaluation_depth": policy.evaluation_depth,
            "waves": [{"key": wave.key, "priority": wave.priority} for wave in policy.ordered_waves],
        }
    validate_discovery_settings(
        status=settings.status,
        cadence_minutes=settings.cadence_minutes,
        target_opportunities=settings.target_opportunities,
        max_job_age_days=settings.max_job_age_days,
        evaluation_depth=settings.evaluation_depth,
        source_filters=settings.source_filters,
    )
    policy = DiscoveryPolicy(
        target_opportunities=settings.target_opportunities,
        max_job_age_days=settings.max_job_age_days,
        minimum_verification_required=settings.verification_required,
        evaluation_depth=settings.evaluation_depth,
    )
    return {
        "target_opportunities": policy.target_opportunities,
        "max_job_age_days": policy.max_job_age_days,
        "verification_required": policy.minimum_verification_required,
        "evaluation_depth": policy.evaluation_depth,
        "source_filters": settings.source_filters,
        "waves": [{"key": wave.key, "priority": wave.priority} for wave in policy.ordered_waves],
    }


def create_discovery_mission(
    db: Session,
    *,
    candidate_profile_id: UUID,
    idempotency_key: str,
    trigger_type: str = "MANUAL",
    settings_id: UUID | None = None,
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
            policy_snapshot=policy_snapshot(settings),
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
        )
        mission_ids.append(mission.id)
        settings.last_scheduled_at = current
        next_run = scheduled_slot + timedelta(minutes=settings.cadence_minutes)
        while next_run <= current:
            next_run += timedelta(minutes=settings.cadence_minutes)
        settings.next_run_at = next_run
    db.commit()
    return mission_ids
