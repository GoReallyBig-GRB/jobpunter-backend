"""Protected system-governance endpoints. Admin grants are operator-managed."""
from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.deps import get_principal
from app.application.ai.task_registry import AI_TASK_REGISTRY
from app.infrastructure.db.models.governance import AIExecution, AdminAuditEvent, AdminGrant, GovernanceConfigVersion
from app.infrastructure.db.models.identity import User
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/admin", tags=["admin-governance"])
SECRET_KEYS = {"apikey", "secret", "secretkey", "password", "token", "accesstoken", "refreshtoken", "credential", "credentials", "privatekey", "signingkey", "authorization", "bearer"}

class ConfigDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_key: str = Field(min_length=4, max_length=160, pattern=r"^(ai\.task|system)\.[a-z0-9_.-]{1,140}$")
    payload: dict[str, Any] = Field(min_length=1)
    supersedes_id: UUID | None = None

class AuditEventResponse(BaseModel):
    id: UUID
    actor_user_id: UUID
    action: str
    resource_type: str
    resource_id: UUID | None
    outcome: str
    details: dict[str, Any]
    request_id: str | None
    created_at: datetime

class AIExecutionResponse(BaseModel):
    id: UUID
    user_id: UUID
    task: str
    config_version_id: UUID | None
    provider: str
    model: str
    status: str
    latency_ms: int | None
    error_code: str | None
    request_id: str | None
    created_at: datetime
    finished_at: datetime | None

class ConfigVersionResponse(BaseModel):
    id: UUID
    config_key: str
    version: int
    status: str
    payload: dict[str, Any]
    created_by_user_id: UUID
    created_at: datetime
    published_by_user_id: UUID | None
    published_at: datetime | None
    supersedes_id: UUID | None

def _validate_config_payload(config_key: str, payload: dict[str, Any]) -> None:
    if not config_key.startswith("ai.task."):
        return
    task_id = config_key.removeprefix("ai.task.")
    if task_id not in AI_TASK_REGISTRY:
        raise HTTPException(status_code=422, detail="Unknown AI task configuration key.")
    allowed = {"system_instruction", "model", "temperature"}
    if set(payload) - allowed:
        raise HTTPException(status_code=422, detail="AI task configs support only system_instruction, model, and temperature.")
    instruction = payload.get("system_instruction")
    if instruction is not None and (not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 12000):
        raise HTTPException(status_code=422, detail="system_instruction must be non-empty text up to 12000 characters.")
    model = payload.get("model")
    if model is not None and (not isinstance(model, str) or not re.fullmatch(r"gemini-[A-Za-z0-9._-]{1,100}", model)):
        raise HTTPException(status_code=422, detail="Only approved Gemini model identifiers are accepted.")
    temperature = payload.get("temperature")
    if temperature is not None and (isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 1):
        raise HTTPException(status_code=422, detail="temperature must be a number from 0 to 1.")

def _require_admin(db: Session, principal: AuthenticatedPrincipal) -> None:
    grant = db.scalar(select(AdminGrant).join(User, User.id == AdminGrant.user_id).where(
        AdminGrant.user_id == principal.user_id, AdminGrant.role == "ADMIN",
        AdminGrant.active.is_(True), User.status == "ACTIVE"))
    if grant is None:
        raise HTTPException(status_code=403, detail="Administrator permission required.")

def _reject_secrets(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized in SECRET_KEYS or normalized.endswith(("apikey", "secret", "secretkey", "password", "token", "credential", "credentials", "privatekey", "signingkey", "authorization", "bearer")):
                raise HTTPException(status_code=422, detail="Secrets and credentials must not be stored in governance payloads.")
            _reject_secrets(nested)
    elif isinstance(value, list):
        for item in value:
            _reject_secrets(item)

def _audit(db: Session, actor_id: UUID, action: str, resource_id: UUID, request: Request, details: dict[str, Any]) -> None:
    db.add(AdminAuditEvent(actor_user_id=actor_id, action=action,
        resource_type="governance_config_version", resource_id=resource_id, outcome="SUCCESS",
        details=details, request_id=request.headers.get("x-request-id"), created_at=datetime.now(timezone.utc)))

def _as_dict(row: GovernanceConfigVersion) -> dict[str, Any]:
    return {key: getattr(row, key) for key in (
        "id", "config_key", "version", "status", "payload", "created_by_user_id",
        "created_at", "published_by_user_id", "published_at", "supersedes_id")}


@router.get("/ai-executions", response_model=list[AIExecutionResponse])
def list_ai_executions(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    task: str | None = None,
    execution_status: str | None = None,
    user_id: UUID | None = None,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    _require_admin(db, principal)
    if limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(status_code=422, detail="limit must be 1-100 and offset must be non-negative.")
    if task is not None and task not in {
        "document_parsing", "profile_extraction", "jd_extraction", "requirement_classification",
        "evidence_matching", "career_pathway_discovery", "cv_generation", "cover_letter",
        "application_questions", "suggested_answers",
    }:
        raise HTTPException(status_code=422, detail="Unknown AI task.")
    if execution_status is not None and execution_status not in {"RUNNING", "SUCCEEDED", "FAILED"}:
        raise HTTPException(status_code=422, detail="Unknown AI execution status.")
    query = select(AIExecution)
    if task is not None:
        query = query.where(AIExecution.task == task)
    if execution_status is not None:
        query = query.where(AIExecution.status == execution_status)
    if user_id is not None:
        query = query.where(AIExecution.user_id == user_id)
    rows = db.scalars(query.order_by(
        AIExecution.created_at.desc(), AIExecution.id.desc()
    ).limit(limit).offset(offset)).all()
    # Deliberately omit prompts and generated outputs from administrative telemetry.
    return [{
        "id": row.id, "user_id": row.user_id, "task": row.task,
        "config_version_id": row.config_version_id, "provider": row.provider,
        "model": row.model, "status": row.status, "latency_ms": row.latency_ms,
        "error_code": row.error_code, "request_id": row.request_id,
        "created_at": row.created_at, "finished_at": row.finished_at,
    } for row in rows]

@router.get("/audit", response_model=list[AuditEventResponse])
def list_admin_audit_events(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    _require_admin(db, principal)
    if limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(status_code=422, detail="limit must be 1-100 and offset must be non-negative.")
    rows = db.scalars(select(AdminAuditEvent).order_by(
        AdminAuditEvent.created_at.desc(), AdminAuditEvent.id.desc()
    ).limit(limit).offset(offset)).all()
    return [{
        "id": row.id, "actor_user_id": row.actor_user_id, "action": row.action,
        "resource_type": row.resource_type, "resource_id": row.resource_id,
        "outcome": row.outcome, "details": row.details, "request_id": row.request_id,
        "created_at": row.created_at,
    } for row in rows]

@router.get("/configs", response_model=list[ConfigVersionResponse])
def list_config_versions(request: Request, principal: AuthenticatedPrincipal = Depends(get_principal),
                         db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    _require_admin(db, principal)
    rows = db.scalars(select(GovernanceConfigVersion).order_by(
        GovernanceConfigVersion.config_key, GovernanceConfigVersion.version.desc())).all()
    return [_as_dict(row) for row in rows]

@router.post("/configs", response_model=ConfigVersionResponse, status_code=status.HTTP_201_CREATED)
def create_config_draft(payload: ConfigDraftRequest, request: Request,
                         principal: AuthenticatedPrincipal = Depends(get_principal),
                         db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_admin(db, principal)
    _reject_secrets(payload.payload)
    _validate_config_payload(payload.config_key, payload.payload)
    if payload.supersedes_id is not None:
        source = db.get(GovernanceConfigVersion, payload.supersedes_id)
        if source is None or source.config_key != payload.config_key or source.status not in {"PUBLISHED", "RETIRED"}:
            raise HTTPException(status_code=422, detail="Rollback source must be a published version of the same config key.")
    latest = db.scalar(select(func.max(GovernanceConfigVersion.version)).where(
        GovernanceConfigVersion.config_key == payload.config_key))
    row = GovernanceConfigVersion(config_key=payload.config_key, version=(latest or 0) + 1,
        status="DRAFT", payload=payload.payload, created_by_user_id=principal.user_id,
        created_at=datetime.now(timezone.utc), supersedes_id=payload.supersedes_id)
    db.add(row)
    try:
        db.flush()
        _audit(db, principal.user_id, "CONFIG_DRAFT_CREATED", row.id, request,
               {"config_key": row.config_key, "version": row.version,
                "supersedes_id": str(payload.supersedes_id) if payload.supersedes_id else None})
        db.commit()
        db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A configuration version was created concurrently; retry.") from exc
    return _as_dict(row)

@router.post("/configs/{config_id}/publish", response_model=ConfigVersionResponse)
def publish_config_draft(config_id: UUID, request: Request,
                         principal: AuthenticatedPrincipal = Depends(get_principal),
                         db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_admin(db, principal)
    row = db.scalar(select(GovernanceConfigVersion).where(
        GovernanceConfigVersion.id == config_id).with_for_update())
    if row is None:
        raise HTTPException(status_code=404, detail="Configuration version not found.")
    if row.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only draft configuration versions can be published.")
    current = db.scalars(select(GovernanceConfigVersion).where(
        GovernanceConfigVersion.config_key == row.config_key,
        GovernanceConfigVersion.status == "PUBLISHED",
        GovernanceConfigVersion.id != row.id).with_for_update()).all()
    now = datetime.now(timezone.utc)
    for prior in current:
        prior.status = "RETIRED"
    # Flush retirement first so the one-published-version partial unique index
    # cannot observe both versions as PUBLISHED during the same unit-of-work flush.
    if current:
        db.flush()
    row.status = "PUBLISHED"
    row.published_by_user_id = principal.user_id
    row.published_at = now
    _audit(db, principal.user_id, "CONFIG_VERSION_PUBLISHED", row.id, request,
           {"config_key": row.config_key, "version": row.version,
            "retired_versions": [str(item.id) for item in current]})
    try:
        db.commit()
        db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Another version was published concurrently; retry.") from exc
    return _as_dict(row)

@router.post("/configs/{config_id}/rollback", response_model=ConfigVersionResponse,
             status_code=status.HTTP_201_CREATED)
def create_rollback_draft(config_id: UUID, request: Request,
                           principal: AuthenticatedPrincipal = Depends(get_principal),
                           db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_admin(db, principal)
    source = db.get(GovernanceConfigVersion, config_id)
    if source is None or source.status not in {"PUBLISHED", "RETIRED"}:
        raise HTTPException(status_code=404, detail="Published configuration version not found.")
    latest = db.scalar(select(func.max(GovernanceConfigVersion.version)).where(
        GovernanceConfigVersion.config_key == source.config_key))
    row = GovernanceConfigVersion(config_key=source.config_key, version=(latest or 0) + 1,
        status="DRAFT", payload=source.payload, created_by_user_id=principal.user_id,
        created_at=datetime.now(timezone.utc), supersedes_id=source.id)
    db.add(row)
    try:
        db.flush()
        _audit(db, principal.user_id, "CONFIG_ROLLBACK_DRAFT_CREATED", row.id, request,
               {"config_key": row.config_key, "version": row.version, "copied_from_version": source.version})
        db.commit()
        db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A configuration version was created concurrently; retry.") from exc
    return _as_dict(row)


class AdminGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    role: str = Field(default="ADMIN", pattern=r"^ADMIN$")


class AdminGrantResponse(BaseModel):
    id: UUID
    user_id: UUID
    role: str
    active: bool
    granted_by_user_id: UUID | None
    created_at: datetime
    revoked_at: datetime | None


def _admin_grant_dict(row: AdminGrant) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "role": row.role,
        "active": row.active,
        "granted_by_user_id": row.granted_by_user_id,
        "created_at": row.created_at,
        "revoked_at": row.revoked_at,
    }


def _audit_admin_grant(
    db: Session,
    actor_id: UUID,
    action: str,
    grant: AdminGrant,
    request: Request,
    details: dict[str, Any],
) -> None:
    db.add(AdminAuditEvent(
        actor_user_id=actor_id,
        action=action,
        resource_type="admin_grant",
        resource_id=grant.id,
        outcome="SUCCESS",
        details=details,
        request_id=request.headers.get("x-request-id"),
        created_at=datetime.now(timezone.utc),
    ))


@router.get("/grants", response_model=list[AdminGrantResponse])
def list_admin_grants(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    _require_admin(db, principal)
    rows = db.scalars(select(AdminGrant).order_by(
        AdminGrant.created_at, AdminGrant.id,
    )).all()
    return [_admin_grant_dict(row) for row in rows]


@router.post("/grants", response_model=AdminGrantResponse, status_code=status.HTTP_201_CREATED)
def create_or_reactivate_admin_grant(
    payload: AdminGrantRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    # This endpoint cannot bootstrap the first administrator: the caller must
    # already hold an active ADMIN grant. Initial bootstrap remains out-of-band.
    _require_admin(db, principal)
    target = db.get(User, payload.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Target user not found.")
    if target.status != "ACTIVE":
        raise HTTPException(status_code=409, detail="Only active users can receive administrator grants.")

    grant = db.scalar(select(AdminGrant).where(
        AdminGrant.user_id == payload.user_id,
        AdminGrant.role == payload.role,
    ).with_for_update())
    now = datetime.now(timezone.utc)
    if grant is not None and grant.active:
        raise HTTPException(status_code=409, detail="User already has an active administrator grant.")

    if grant is None:
        grant = AdminGrant(
            user_id=target.id,
            role=payload.role,
            active=True,
            granted_by_user_id=principal.user_id,
            created_at=now,
        )
        db.add(grant)
        action = "ADMIN_GRANT_CREATED"
        previous_active = None
    else:
        grant.active = True
        grant.granted_by_user_id = principal.user_id
        grant.revoked_at = None
        action = "ADMIN_GRANT_REACTIVATED"
        previous_active = False

    try:
        db.flush()
        _audit_admin_grant(
            db, principal.user_id, action, grant, request,
            {
                "target_user_id": str(target.id),
                "role": grant.role,
                "previous_active": previous_active,
                "active": True,
            },
        )
        db.commit()
        db.refresh(grant)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Administrator grant changed concurrently; retry.") from exc
    return _admin_grant_dict(grant)


@router.post("/grants/{grant_id}/revoke", response_model=AdminGrantResponse)
def revoke_admin_grant(
    grant_id: UUID,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal)
    active_grants = db.scalars(select(AdminGrant).where(
        AdminGrant.role == "ADMIN",
        AdminGrant.active.is_(True),
    ).order_by(AdminGrant.id).with_for_update()).all()
    if not any(item.user_id == principal.user_id for item in active_grants):
        raise HTTPException(status_code=403, detail="Administrator permission was revoked.")
    grant = next((item for item in active_grants if item.id == grant_id), None)
    if grant is None:
        grant = db.scalar(select(AdminGrant).where(AdminGrant.id == grant_id).with_for_update())
    if grant is None:
        raise HTTPException(status_code=404, detail="Administrator grant not found.")
    if not grant.active:
        raise HTTPException(status_code=409, detail="Administrator grant is already inactive.")
    if len(active_grants) <= 1:
        raise HTTPException(status_code=409, detail="The final active administrator grant cannot be revoked.")

    now = datetime.now(timezone.utc)
    grant.active = False
    grant.revoked_at = now
    _audit_admin_grant(
        db, principal.user_id, "ADMIN_GRANT_REVOKED", grant, request,
        {
            "target_user_id": str(grant.user_id),
            "role": grant.role,
            "previous_active": True,
            "active": False,
            "revoked_at": now.isoformat(),
        },
    )
    db.commit()
    db.refresh(grant)
    return _admin_grant_dict(grant)
