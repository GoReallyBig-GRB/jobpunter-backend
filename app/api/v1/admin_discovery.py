"""Administrator endpoints for durable global discovery schedules and runs."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.api.v1.admin import _audit, _capture_admin_audit_context, _require_admin
from app.application.jobs.adaptive_dispatcher import AdaptiveAcquisitionDispatcher
from app.application.jobs.discovery_intake import (
    approve_discovery_candidate,
    intake_discovered_urls,
    mark_discovery_candidate_acquired,
)
from app.application.jobs.discovery_schedules import (
    DiscoveryScheduleConfiguration,
    create_discovery_run,
    next_schedule_at,
    schedule_configuration,
)
from app.application.jobs.resolved_url_acquisition import ResolvedUrlAcquisitionService
from app.application.jobs.source_intelligence import (
    approve_source_intelligence,
    record_source_evidence,
)
from app.application.jobs.url_source_resolver import RetrievalRoute
from app.infrastructure.acquisition.dispatch_ledger import SqlAlchemyDispatchAttemptLedger
from app.infrastructure.db.models.discovery_orchestration import (
    DiscoveryRun,
    DiscoverySchedule,
    DiscoveryStrategyVersion,
)
from app.infrastructure.db.models.source_intelligence import (
    SourceDiscoveryCandidate,
    SourceIntelligenceProfile,
)
from app.infrastructure.db.models.work_item import WorkItem
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
from app.infrastructure.db.session import get_db
from app.ports.discovery import DiscoveredUrl
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(
    prefix="/admin/discovery", tags=["admin-discovery"],
    dependencies=[Depends(_capture_admin_audit_context)],
)
PRINCIPAL_DEPENDENCY = Depends(get_principal)
DB_DEPENDENCY = Depends(get_db)
PAGE_LIMIT = Query(50, ge=1, le=200)


class ScheduleCreateRequest(DiscoveryScheduleConfiguration):
    reason: str = Field(min_length=3, max_length=1000)
    activate: bool = False

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reason must not be blank")
        return value


class ScheduleStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ACTIVE", "PAUSED", "RETIRED"]
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)


class ManualRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    reason: str = Field(min_length=3, max_length=1000)


class CancelRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)


class DiscoveryIntakeItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)
    source_domain: str = Field(min_length=1, max_length=253)
    market_scope: Literal["LOCAL", "INTERNATIONAL"]
    nigeria_eligible: bool | None = None
    evidence: list[str] = Field(default_factory=list, max_length=20)


class DiscoveryIntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    items: list[DiscoveryIntakeItemRequest] = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=3, max_length=1000)


class SourceIntelligenceReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVED", "REJECTED", "PAUSED"]
    expected_revision: int = Field(ge=1)
    capabilities: dict = Field(default_factory=dict)
    permission_evidence: dict = Field(default_factory=dict)
    reason: str = Field(min_length=3, max_length=1000)


class SourceCandidateReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVED", "REJECTED", "PAUSED"]
    expected_revision: int = Field(ge=1)
    source_id: UUID | None = None
    reason: str = Field(min_length=3, max_length=1000)


class SourceCandidateAcquisitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: UUID
    connector_ids: dict[str, UUID]
    operation_key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    max_attempts: int = Field(default=3, ge=1, le=5)
    reason: str = Field(min_length=3, max_length=1000)


def _schedule_dict(row: DiscoverySchedule) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _run_dict(row: DiscoveryRun) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _model_dict(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


@router.post("/source-candidates/intake", status_code=201)
def intake_source_candidates(
    payload: DiscoveryIntakeRequest, request: Request,
    principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    _require_admin(db, principal, request)
    discoveries = tuple(
        DiscoveredUrl(
            url=item.url,
            provider_key=payload.provider_key,
            source_domain=item.source_domain,
            market_scope=item.market_scope,
            nigeria_eligible=item.nigeria_eligible,
            evidence=tuple(item.evidence),
        )
        for item in payload.items
    )
    try:
        result = intake_discovered_urls(db, discoveries)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(db, principal.user_id, "SOURCE_DISCOVERY_CANDIDATES_INTAKED", None, request, {
        "provider_key": payload.provider_key.strip().upper(),
        "created_count": result.created_count,
        "refreshed_count": result.refreshed_count,
        "reason": payload.reason,
    }, resource_type="source_discovery_candidate")
    db.commit()
    return {
        "data": [_model_dict(row) for row in result.candidates],
        "summary": {
            "created_count": result.created_count,
            "refreshed_count": result.refreshed_count,
        },
    }


@router.get("/source-intelligence")
def list_source_intelligence(
    request: Request, status: str | None = None, limit: int = PAGE_LIMIT,
    principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    _require_admin(db, principal, request)
    statement = select(SourceIntelligenceProfile)
    if status is not None:
        normalized = status.strip().upper()
        if normalized not in {"REVIEW_REQUIRED", "APPROVED", "REJECTED", "PAUSED"}:
            raise HTTPException(status_code=422, detail="Unsupported source intelligence status.")
        statement = statement.where(SourceIntelligenceProfile.status == normalized)
    rows = db.scalars(
        statement.order_by(SourceIntelligenceProfile.updated_at.desc()).limit(limit)
    ).all()
    return {"data": [_model_dict(row) for row in rows], "pagination": {"limit": limit}}


@router.patch("/source-intelligence/{profile_id}/review")
def review_source_intelligence(
    profile_id: UUID, payload: SourceIntelligenceReviewRequest, request: Request,
    principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    _require_admin(db, principal, request)
    profile = db.scalar(
        select(SourceIntelligenceProfile)
        .where(SourceIntelligenceProfile.id == profile_id)
        .with_for_update()
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Source intelligence profile not found.")
    if profile.revision != payload.expected_revision:
        raise HTTPException(status_code=409, detail="Source intelligence revision conflict.")
    previous = profile.status
    try:
        if payload.capabilities:
            record_source_evidence(db, profile, "CAPABILITY", payload.capabilities)
        if payload.permission_evidence:
            record_source_evidence(db, profile, "PERMISSION", payload.permission_evidence)
        if payload.decision == "APPROVED":
            approve_source_intelligence(db, profile, reviewer_user_id=principal.user_id)
        else:
            profile.status = payload.decision
            profile.approved_by_user_id = None
            profile.approved_at = None
            profile.revision += 1
            db.flush()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(db, principal.user_id, "SOURCE_INTELLIGENCE_REVIEWED", profile.id, request, {
        "from": previous,
        "to": profile.status,
        "expected_revision": payload.expected_revision,
        "reason": payload.reason,
    }, resource_type="source_intelligence_profile")
    db.commit()
    db.refresh(profile)
    return {"data": _model_dict(profile)}


@router.get("/source-candidates")
def list_source_candidates(
    request: Request, status: str | None = None, profile_id: UUID | None = None,
    limit: int = PAGE_LIMIT,
    principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    _require_admin(db, principal, request)
    statement = select(SourceDiscoveryCandidate)
    if status is not None:
        normalized = status.strip().upper()
        if normalized not in {
            "REVIEW_REQUIRED", "APPROVED", "PROCESSING", "REJECTED",
            "PAUSED", "FAILED", "ACQUIRED",
        }:
            raise HTTPException(status_code=422, detail="Unsupported source candidate status.")
        statement = statement.where(SourceDiscoveryCandidate.status == normalized)
    if profile_id is not None:
        statement = statement.where(SourceDiscoveryCandidate.profile_id == profile_id)
    rows = db.scalars(
        statement.order_by(SourceDiscoveryCandidate.last_discovered_at.desc()).limit(limit)
    ).all()
    return {"data": [_model_dict(row) for row in rows], "pagination": {"limit": limit}}


@router.patch("/source-candidates/{candidate_id}/review")
def review_source_candidate(
    candidate_id: UUID, payload: SourceCandidateReviewRequest, request: Request,
    principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    _require_admin(db, principal, request)
    candidate = db.scalar(
        select(SourceDiscoveryCandidate)
        .where(SourceDiscoveryCandidate.id == candidate_id)
        .with_for_update()
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Source discovery candidate not found.")
    if candidate.revision != payload.expected_revision:
        raise HTTPException(status_code=409, detail="Source discovery candidate revision conflict.")
    previous = candidate.status
    if payload.decision == "APPROVED":
        if payload.source_id is None:
            raise HTTPException(status_code=422, detail="Approved candidates require a source_id.")
        profile = db.get(SourceIntelligenceProfile, candidate.profile_id)
        if profile is None:
            raise HTTPException(status_code=409, detail="Source intelligence profile is missing.")
        try:
            approve_discovery_candidate(
                db, candidate, profile,
                reviewer_user_id=principal.user_id, source_id=payload.source_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        candidate.status = payload.decision
        candidate.approved_by_user_id = None
        candidate.approved_at = None
        candidate.revision += 1
        db.flush()
    _audit(db, principal.user_id, "SOURCE_DISCOVERY_CANDIDATE_REVIEWED", candidate.id, request, {
        "from": previous,
        "to": candidate.status,
        "expected_revision": payload.expected_revision,
        "reason": payload.reason,
    }, resource_type="source_discovery_candidate")
    db.commit()
    db.refresh(candidate)
    return {"data": _model_dict(candidate)}


@router.post("/source-candidates/{candidate_id}/acquire")
def acquire_source_candidate(
    candidate_id: UUID, payload: SourceCandidateAcquisitionRequest, request: Request,
    principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    _require_admin(db, principal, request)
    candidate = db.scalar(
        select(SourceDiscoveryCandidate)
        .where(SourceDiscoveryCandidate.id == candidate_id)
        .with_for_update()
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Source discovery candidate not found.")
    if candidate.status != "APPROVED":
        raise HTTPException(status_code=409, detail="Only approved candidates can be acquired.")
    profile = db.get(SourceIntelligenceProfile, candidate.profile_id)
    if profile is None:
        raise HTTPException(status_code=409, detail="Source intelligence profile is missing.")
    if candidate.source_id is not None and candidate.source_id != payload.source_id:
        raise HTTPException(status_code=409, detail="Candidate is bound to a different source.")
    try:
        connector_ids = {
            RetrievalRoute(route.strip().upper()): connector_id
            for route, connector_id in payload.connector_ids.items()
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Unsupported candidate connector route.") from exc
    if RetrievalRoute.REVIEW_REQUIRED in connector_ids:
        raise HTTPException(status_code=422, detail="Review-required is not an executable route.")
    service = ResolvedUrlAcquisitionService(
        SqlAlchemyAcquisitionRepository(db),
        AdaptiveAcquisitionDispatcher(
            SqlAlchemyDispatchAttemptLedger(db, profile_id=profile.id)
        ),
    )
    try:
        result = service.execute_candidate(
            source_id=payload.source_id,
            profile=profile,
            candidate=candidate,
            operation_key=payload.operation_key,
            connector_ids=connector_ids,
            max_attempts=payload.max_attempts,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    acquired_count = sum(item.acquired_count for item in result.attempts)
    if result.selected_route is not None and acquired_count > 0:
        mark_discovery_candidate_acquired(db, candidate)
    _audit(db, principal.user_id, "SOURCE_DISCOVERY_CANDIDATE_ACQUIRED", candidate.id, request, {
        "source_id": str(payload.source_id),
        "status": result.status.value,
        "selected_route": result.selected_route.value if result.selected_route else None,
        "attempt_count": len(result.attempts),
        "acquired_count": acquired_count,
        "reason": payload.reason,
    }, resource_type="source_discovery_candidate")
    db.commit()
    db.refresh(candidate)
    return {
        "data": {
            "candidate": _model_dict(candidate),
            "status": result.status.value,
            "selected_route": result.selected_route.value if result.selected_route else None,
            "error_category": result.error_category,
            "attempts": [
                {
                    "route": item.route.value,
                    "status": item.status.value,
                    "error_category": item.error_category,
                    "acquired_count": item.acquired_count,
                }
                for item in result.attempts
            ],
        }
    }


@router.post("/schedules", status_code=201)
def create_schedule(
    payload: ScheduleCreateRequest, request: Request,
    principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    _require_admin(db, principal, request)
    strategy = db.get(DiscoveryStrategyVersion, payload.strategy_version_id)
    if strategy is None or strategy.status != "PUBLISHED":
        raise HTTPException(status_code=422, detail="A published discovery strategy version is required.")
    now = datetime.now(UTC)
    config = DiscoveryScheduleConfiguration.model_validate(
        payload.model_dump(exclude={"reason", "activate"})
    )
    row = DiscoverySchedule(
        **config.model_dump(mode="python"), status="ACTIVE" if payload.activate else "PAUSED",
        next_run_at=next_schedule_at(config, after=now) if payload.activate else None,
        created_by_user_id=principal.user_id, updated_by_user_id=principal.user_id,
        created_at=now, updated_at=now,
    )
    db.add(row)
    db.flush()
    _audit(db, principal.user_id, "DISCOVERY_SCHEDULE_CREATED", row.id, request, {
        "status": row.status, "schedule_type": row.schedule_type,
        "strategy_version_id": str(row.strategy_version_id), "reason": payload.reason,
    }, resource_type="discovery_schedule")
    db.commit()
    db.refresh(row)
    return {"data": _schedule_dict(row)}


@router.get("/schedules")
def list_schedules(
    request: Request, status: str | None = None, limit: int = PAGE_LIMIT,
    principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    _require_admin(db, principal, request)
    statement = select(DiscoverySchedule)
    if status is not None:
        if status not in {"ACTIVE", "PAUSED", "RETIRED"}:
            raise HTTPException(status_code=422, detail="Unsupported schedule status.")
        statement = statement.where(DiscoverySchedule.status == status)
    rows = db.scalars(statement.order_by(DiscoverySchedule.created_at.desc()).limit(limit)).all()
    return {"data": [_schedule_dict(row) for row in rows], "pagination": {"limit": limit}}


@router.patch("/schedules/{schedule_id}/status")
def change_schedule_status(
    schedule_id: UUID, payload: ScheduleStatusRequest, request: Request,
    principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    _require_admin(db, principal, request)
    row = db.scalar(select(DiscoverySchedule).where(DiscoverySchedule.id == schedule_id).with_for_update())
    if row is None:
        raise HTTPException(status_code=404, detail="Discovery schedule not found.")
    if row.revision != payload.expected_revision:
        raise HTTPException(status_code=409, detail="Discovery schedule revision conflict.")
    if row.status == "RETIRED" and payload.status != "RETIRED":
        raise HTTPException(status_code=409, detail="Retired discovery schedules cannot be reactivated.")
    previous = row.status
    row.status = payload.status
    row.updated_by_user_id = principal.user_id
    row.updated_at = datetime.now(UTC)
    row.revision += 1
    if payload.status == "ACTIVE":
        row.next_run_at = next_schedule_at(schedule_configuration(row), after=row.updated_at)
    else:
        row.next_run_at = None
    _audit(db, principal.user_id, "DISCOVERY_SCHEDULE_STATUS_CHANGED", row.id, request, {
        "from": previous, "to": row.status, "reason": payload.reason,
        "expected_revision": payload.expected_revision,
    }, resource_type="discovery_schedule")
    db.commit()
    db.refresh(row)
    return {"data": _schedule_dict(row)}


@router.post("/schedules/{schedule_id}/runs", status_code=201)
def start_manual_run(
    schedule_id: UUID, payload: ManualRunRequest, request: Request,
    principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    _require_admin(db, principal, request)
    schedule = db.get(DiscoverySchedule, schedule_id)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Discovery schedule not found.")
    if schedule.status == "RETIRED" or not schedule.manual_execution_enabled:
        raise HTTPException(status_code=409, detail="Manual execution is disabled for this schedule.")
    run = create_discovery_run(
        db, schedule=schedule, trigger_type="MANUAL",
        run_key=f"manual:{schedule.id}:{payload.idempotency_key}",
        requested_by_user_id=principal.user_id, reason=payload.reason,
    )
    _audit(db, principal.user_id, "DISCOVERY_RUN_REQUESTED", run.id, request, {
        "schedule_id": str(schedule.id), "idempotency_key": payload.idempotency_key,
        "reason": payload.reason,
    }, resource_type="discovery_run")
    db.commit()
    db.refresh(run)
    return {"data": _run_dict(run)}


@router.get("/runs")
def list_runs(
    request: Request, status: str | None = None, schedule_id: UUID | None = None,
    limit: int = PAGE_LIMIT,
    principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    _require_admin(db, principal, request)
    statement = select(DiscoveryRun)
    if status is not None:
        statement = statement.where(DiscoveryRun.status == status)
    if schedule_id is not None:
        statement = statement.where(DiscoveryRun.schedule_id == schedule_id)
    rows = db.scalars(statement.order_by(DiscoveryRun.created_at.desc()).limit(limit)).all()
    return {"data": [_run_dict(row) for row in rows], "pagination": {"limit": limit}}


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: UUID, payload: CancelRunRequest, request: Request,
    principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    _require_admin(db, principal, request)
    run = db.scalar(select(DiscoveryRun).where(DiscoveryRun.id == run_id).with_for_update())
    if run is None:
        raise HTTPException(status_code=404, detail="Discovery run not found.")
    if run.revision != payload.expected_revision:
        raise HTTPException(status_code=409, detail="Discovery run revision conflict.")
    if run.status not in {"PENDING", "RUNNING"}:
        raise HTTPException(status_code=409, detail="Only pending or running discovery runs can be cancelled.")
    previous = run.status
    now = datetime.now(UTC)
    run.status = "CANCELLED"
    run.completed_at = now
    run.reason = payload.reason
    run.revision += 1
    item = db.scalar(select(WorkItem).where(
        WorkItem.work_type == "DISCOVERY_RUN", WorkItem.resource_id == run.id,
        WorkItem.status.in_(("PENDING", "PROCESSING")),
    ).with_for_update())
    if item is not None:
        item.status = "CANCELLED"
        item.completed_at = now
        item.result = {"reason": payload.reason, "cancelled_by": str(principal.user_id)}
        item.revision += 1
    _audit(db, principal.user_id, "DISCOVERY_RUN_CANCELLED", run.id, request, {
        "from": previous, "reason": payload.reason,
    }, resource_type="discovery_run")
    db.commit()
    db.refresh(run)
    return {"data": _run_dict(run)}
