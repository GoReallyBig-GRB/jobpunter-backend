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
from app.application.jobs.discovery_schedules import (
    DiscoveryScheduleConfiguration,
    create_discovery_run,
    next_schedule_at,
    schedule_configuration,
)
from app.infrastructure.db.models.discovery_orchestration import (
    DiscoveryRun,
    DiscoverySchedule,
    DiscoveryStrategyVersion,
)
from app.infrastructure.db.models.work_item import WorkItem
from app.infrastructure.db.session import get_db
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


def _schedule_dict(row: DiscoverySchedule) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def _run_dict(row: DiscoveryRun) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


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
