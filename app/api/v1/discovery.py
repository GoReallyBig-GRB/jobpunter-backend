"""Candidate-scoped discovery settings and mission APIs."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.api.errors import DomainError
from app.application.candidates.service import CandidateService
from app.application.jobs.discovery_missions import (
    create_discovery_mission,
    validate_discovery_settings,
)
from app.application.jobs.discovery_schemas import (
    DiscoveryMissionCreate,
    DiscoveryMissionResponse,
    DiscoverySettingsResponse,
    DiscoverySettingsUpdate,
)
from app.infrastructure.db.models import (
    CandidateDiscoverySettings,
    CandidateProfile,
    DiscoveryMission,
)
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate/discovery", tags=["candidate-discovery"])


def _profile_id(db: Session, user_id: UUID) -> UUID:
    profile = CandidateService(db).get_or_create_profile(user_id)
    # Serialize settings creation/update for this candidate, including the
    # first request where no settings row exists yet.
    locked = db.scalar(select(CandidateProfile).where(
        CandidateProfile.id == profile.id,
        CandidateProfile.user_id == user_id,
    ).with_for_update())
    if locked is None:
        raise DomainError("CANDIDATE_PROFILE_NOT_FOUND", "Candidate profile not found", 404)
    return locked.id


def _settings_response(settings: CandidateDiscoverySettings) -> dict:
    return DiscoverySettingsResponse.model_validate(
        settings, from_attributes=True
    ).model_dump(mode="json")


def _mission_response(mission: DiscoveryMission) -> dict:
    return DiscoveryMissionResponse.model_validate(
        mission, from_attributes=True
    ).model_dump(mode="json")


@router.get("/settings", response_model=dict)
def get_settings(
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    profile_id = _profile_id(db, principal.user_id)
    settings = db.scalar(select(CandidateDiscoverySettings).where(
        CandidateDiscoverySettings.candidate_profile_id == profile_id
    ))
    if settings is None:
        settings = CandidateDiscoverySettings(
            candidate_profile_id=profile_id,
            status="DISABLED",
            cadence_minutes=1440,
            target_opportunities=20,
            max_job_age_days=21,
            verification_required=True,
            evaluation_depth="preliminary",
            source_filters={},
            revision=1,
        )
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return {"data": _settings_response(settings)}


@router.put("/settings", response_model=dict)
def update_settings(
    payload: DiscoverySettingsUpdate,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    profile_id = _profile_id(db, principal.user_id)
    try:
        validate_discovery_settings(
            status=payload.status,
            cadence_minutes=payload.cadence_minutes,
            target_opportunities=payload.target_opportunities,
            max_job_age_days=payload.max_job_age_days,
            evaluation_depth=payload.evaluation_depth,
            source_filters=payload.source_filters,
        )
    except ValueError as exc:
        raise DomainError("INVALID_DISCOVERY_SETTINGS", str(exc), 422) from exc

    settings = db.scalar(select(CandidateDiscoverySettings).where(
        CandidateDiscoverySettings.candidate_profile_id == profile_id
    ).with_for_update())
    if settings is None:
        if payload.expected_revision is not None:
            raise DomainError(
                "REVISION_CONFLICT",
                "Discovery settings do not exist at the supplied revision.",
                409,
                {"current_revision": 0},
            )
        settings = CandidateDiscoverySettings(
            candidate_profile_id=profile_id,
            status=payload.status,
            cadence_minutes=payload.cadence_minutes,
            target_opportunities=payload.target_opportunities,
            max_job_age_days=payload.max_job_age_days,
            verification_required=payload.verification_required,
            evaluation_depth=payload.evaluation_depth,
            source_filters=payload.source_filters,
            next_run_at=(
                datetime.now(timezone.utc) if payload.status == "ENABLED" else None
            ),
            revision=1,
        )
        db.add(settings)
    else:
        if payload.expected_revision is None or payload.expected_revision != settings.revision:
            raise DomainError(
                "REVISION_CONFLICT",
                "Discovery settings revision is stale.",
                409,
                {"current_revision": settings.revision},
            )
        settings.status = payload.status
        settings.cadence_minutes = payload.cadence_minutes
        settings.target_opportunities = payload.target_opportunities
        settings.max_job_age_days = payload.max_job_age_days
        settings.verification_required = payload.verification_required
        settings.evaluation_depth = payload.evaluation_depth
        settings.source_filters = payload.source_filters
        settings.next_run_at = (
            datetime.now(timezone.utc) if payload.status == "ENABLED" else None
        )
        settings.revision += 1
        settings.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(settings)
    return {"data": _settings_response(settings)}


@router.post("/missions", response_model=dict, status_code=201)
def create_mission(
    payload: DiscoveryMissionCreate,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    profile_id = _profile_id(db, principal.user_id)
    settings = db.scalar(select(CandidateDiscoverySettings).where(
        CandidateDiscoverySettings.candidate_profile_id == profile_id
    ))
    mission = create_discovery_mission(
        db,
        candidate_profile_id=profile_id,
        settings_id=settings.id if settings else None,
        idempotency_key=payload.idempotency_key,
        trigger_type="MANUAL",
    )
    return {"data": _mission_response(mission)}


@router.get("/missions", response_model=dict)
def list_missions(
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    profile_id = _profile_id(db, principal.user_id)
    missions = db.scalars(select(DiscoveryMission).where(
        DiscoveryMission.candidate_profile_id == profile_id
    ).order_by(DiscoveryMission.created_at.desc(), DiscoveryMission.id.desc()).limit(50)).all()
    return {"data": [_mission_response(mission) for mission in missions]}
