"""Protected system-governance endpoints. Admin grants are operator-managed."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.deps import get_principal
from app.application.ai.task_registry import AI_TASK_REGISTRY
from app.infrastructure.db.models.governance import AIExecution, AdminAuditEvent, AdminGrant, GovernanceConfigVersion
from app.application.jobs.service import AcquisitionService
from app.application.jobs.source_governance import assert_automated_acquisition_permitted
from app.infrastructure.connectors.greenhouse import GreenhouseConnectorError, GreenhouseJobBoardConnector
from app.infrastructure.db.models.source import Source, SourceConnector
from app.infrastructure.db.models.acquisition import AcquisitionRun, RawSourceRecord
from app.infrastructure.db.models.outbox import OutboxEvent
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
from app.infrastructure.db.models.identity import User
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/admin", tags=["admin-governance"])
SECRET_KEYS = {"apikey", "secret", "secretkey", "password", "token", "accesstoken", "refreshtoken", "credential", "credentials", "privatekey", "signingkey", "authorization", "bearer"}

class ConfigDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_key: str = Field(min_length=4, max_length=160, pattern=r"^ai\.task\.[a-z0-9_.-]{1,140}$")
    payload: dict[str, Any] = Field(min_length=1)
    supersedes_id: UUID | None = None



class AcquisitionRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_id: UUID
    run_key: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )


class SourceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    board_token: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Source name must not be blank.")
        return value.strip()


class SourceStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(pattern=r"^(ACTIVE|PAUSED|DISABLED)$")

class SourcePermissionReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(pattern=r"^(APPROVED|PENDING|REJECTED)$")
    reference: str | None = Field(default=None, max_length=2000)

    @field_validator("reference")
    @classmethod
    def reference_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Permission review reference must not be blank.")
        return value.strip() if value is not None else None

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
    user_id: UUID | None
    task: str
    task_policy_version: str
    system_policy_version: str
    output_contract_version: str
    config_version_id: UUID | None
    config_fingerprint: str
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
    payload_fingerprint: str
    created_by_user_id: UUID
    created_at: datetime
    published_by_user_id: UUID | None
    published_at: datetime | None
    supersedes_id: UUID | None

def _validate_config_payload(config_key: str, payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or not payload:
        raise HTTPException(status_code=422, detail="Configuration payload must be a non-empty object.")
    if not config_key.startswith("ai.task."):
        raise HTTPException(status_code=422, detail="Unsupported configuration namespace.")
    task_id = config_key.removeprefix("ai.task.")
    if task_id not in AI_TASK_REGISTRY:
        raise HTTPException(status_code=422, detail="Unknown AI task configuration key.")
    allowed = {"system_instruction", "model", "provider", "temperature"}
    if set(payload) - allowed:
        raise HTTPException(status_code=422, detail="AI task configs support only system_instruction, provider, model, and temperature.")
    instruction = payload.get("system_instruction")
    if instruction is not None and (not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 12000):
        raise HTTPException(status_code=422, detail="system_instruction must be non-empty text up to 12000 characters.")
    provider = payload.get("provider")
    if provider is not None and (
        not isinstance(provider, str) or provider not in {"google", "openai"}
    ):
        raise HTTPException(status_code=422, detail="provider must be google or openai.")
    model = payload.get("model")
    if model is not None:
        valid_model = (
            isinstance(model, str)
            and len(model) <= 110
            and (
                (provider == "google" and re.fullmatch(r"gemini-[A-Za-z0-9._-]{1,100}", model))
                or (provider == "openai" and re.fullmatch(r"gpt-[A-Za-z0-9._-]{1,100}", model))
                or (provider is None and re.fullmatch(r"(?:gemini-[A-Za-z0-9._-]{1,100}|gpt-[A-Za-z0-9._-]{1,100})", model))
            )
        )
        if not valid_model:
            raise HTTPException(status_code=422, detail="model must be valid for the selected AI provider.")
    temperature = payload.get("temperature")
    if temperature is not None and (isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 1):
        raise HTTPException(status_code=422, detail="temperature must be a number from 0 to 1.")

def _require_admin(db: Session, principal: AuthenticatedPrincipal) -> None:
    grant = db.scalar(select(AdminGrant).join(User, User.id == AdminGrant.user_id).where(
        AdminGrant.user_id == principal.user_id, AdminGrant.role == "ADMIN",
        AdminGrant.active.is_(True), User.status == "ACTIVE"))
    if grant is None:
        raise HTTPException(status_code=403, detail="Administrator permission required.")

def _validate_ai_task_filter(task: str | None) -> None:
    """Keep telemetry filters aligned with the canonical server-owned task registry."""
    if task is not None and task not in AI_TASK_REGISTRY:
        raise HTTPException(status_code=422, detail="Unknown AI task.")

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

def _payload_fingerprint(payload: dict[str, Any]) -> str:
    """Hash the canonical JSON representation persisted for a config version."""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_dict(row: GovernanceConfigVersion) -> dict[str, Any]:
    return {key: getattr(row, key) for key in (
        "id", "config_key", "version", "status", "payload", "payload_fingerprint",
        "created_by_user_id", "created_at", "published_by_user_id", "published_at",
        "supersedes_id")}


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
    _validate_ai_task_filter(task)
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
        "task_policy_version": row.task_policy_version,
        "system_policy_version": row.system_policy_version,
        "output_contract_version": row.output_contract_version,
        "config_version_id": row.config_version_id,
        "config_fingerprint": row.config_fingerprint, "provider": row.provider,
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
        status="DRAFT", payload=payload.payload,
        payload_fingerprint=_payload_fingerprint(payload.payload),
        created_by_user_id=principal.user_id,
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
def rollback_config_version(config_id: UUID, request: Request,
                            principal: AuthenticatedPrincipal = Depends(get_principal),
                            db: Session = Depends(get_db)) -> dict[str, Any]:
    """Activate a validated prior payload as a new immutable, audited version."""
    _require_admin(db, principal)
    source = db.scalar(select(GovernanceConfigVersion).where(
        GovernanceConfigVersion.id == config_id).with_for_update())
    if source is None or source.status not in {"PUBLISHED", "RETIRED"}:
        raise HTTPException(status_code=404, detail="Published configuration version not found.")
    _reject_secrets(source.payload)
    _validate_config_payload(source.config_key, source.payload)
    current = db.scalars(select(GovernanceConfigVersion).where(
        GovernanceConfigVersion.config_key == source.config_key,
        GovernanceConfigVersion.status == "PUBLISHED",
    ).with_for_update()).all()
    latest = db.scalar(select(func.max(GovernanceConfigVersion.version)).where(
        GovernanceConfigVersion.config_key == source.config_key))
    now = datetime.now(timezone.utc)
    row = GovernanceConfigVersion(
        config_key=source.config_key, version=(latest or 0) + 1, status="PUBLISHED",
        payload=source.payload, payload_fingerprint=_payload_fingerprint(source.payload),
        created_by_user_id=principal.user_id, created_at=now,
        published_by_user_id=principal.user_id, published_at=now, supersedes_id=source.id,
    )
    for prior in current:
        prior.status = "RETIRED"
    try:
        if current:
            db.flush()
        db.add(row)
        db.flush()
        _audit(db, principal.user_id, "CONFIG_ROLLBACK_ACTIVATED", row.id, request, {
            "config_key": row.config_key, "version": row.version,
            "copied_from_version_id": str(source.id), "copied_from_version": source.version,
            "payload_fingerprint": row.payload_fingerprint,
            "retired_versions": [str(item.id) for item in current],
        })
        db.commit()
        db.refresh(row)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A configuration version changed concurrently; retry.") from exc
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


def _source_registry_response(source: Source, connectors: list[SourceConnector]) -> dict[str, Any]:
    return {
        "id": source.id,
        "name": source.name,
        "source_type": source.source_type,
        "base_url": source.base_url,
        "status": source.status,
        "permission_review_status": source.permission_review_status,
        "permission_review_reference": source.permission_review_reference,
        "permission_reviewed_at": source.permission_reviewed_at,
        "operational_state": source.operational_state,
        "last_error_category": source.last_error_category,
        "last_observed_at": source.last_observed_at,
        "created_at": source.created_at,
        "updated_at": source.updated_at,
        "connectors": [{
            "id": connector.id,
            "connector_type": connector.connector_type,
            "connector_version": connector.connector_version,
            "status": connector.status,
            "capabilities": connector.capabilities or {},
            "limits": connector.limits or {},
            "last_verified_at": connector.last_verified_at,
        } for connector in connectors],
    }


def _enforce_source_permission_review(
    source: Source,
    principal: AuthenticatedPrincipal,
    request: Request,
    db: Session,
) -> None:
    try:
        assert_automated_acquisition_permitted(source)
    except ValueError as exc:
        now = datetime.now(timezone.utc)
        source.operational_state = "RESTRICTED"
        source.last_error_category = "PERMISSION_REVIEW_REQUIRED"
        source.updated_at = now
        db.add(AdminAuditEvent(
            actor_user_id=principal.user_id,
            action="SOURCE_ACQUISITION_BLOCKED_PERMISSION_REVIEW",
            resource_type="source",
            resource_id=source.id,
            outcome="BLOCKED",
            details={"permission_review_status": source.permission_review_status},
            request_id=request.headers.get("x-request-id"),
            created_at=now,
        ))
        db.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SOURCE_PERMISSION_REVIEW_REQUIRED",
                "message": "Automated acquisition is blocked until source permission review is approved.",
            },
        ) from exc


@router.get("/sources", response_model=dict)
def list_source_registry(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal)
    sources = db.scalars(select(Source).order_by(Source.created_at, Source.id)).all()
    source_ids = [source.id for source in sources]
    connectors = db.scalars(
        select(SourceConnector)
        .where(SourceConnector.source_id.in_(source_ids))
        .order_by(SourceConnector.created_at, SourceConnector.id)
    ).all() if source_ids else []
    by_source: dict[UUID, list[SourceConnector]] = {}
    for connector in connectors:
        by_source.setdefault(connector.source_id, []).append(connector)
    return {"data": [
        _source_registry_response(source, by_source.get(source.id, []))
        for source in sources
    ]}


@router.post("/sources", response_model=dict, status_code=status.HTTP_201_CREATED)
def create_source_registry_entry(
    payload: SourceCreateRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal)
    try:
        GreenhouseJobBoardConnector.validate_context({"board_token": payload.board_token})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Greenhouse board token is invalid.") from exc

    now = datetime.now(timezone.utc)
    source = Source(
        name=payload.name,
        source_type=GreenhouseJobBoardConnector.connector_type,
        base_url=GreenhouseJobBoardConnector.BASE_URL,
        status="ACTIVE",
        configuration={},
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    try:
        db.flush()
        connector = SourceConnector(
            source_id=source.id,
            connector_type=GreenhouseJobBoardConnector.connector_type,
            connector_version=GreenhouseJobBoardConnector.connector_version,
            status="ACTIVE",
            configuration={"board_token": payload.board_token},
            capabilities={"published_jobs": True},
            limits={"timeout_seconds": GreenhouseJobBoardConnector.TIMEOUT_SECONDS},
            created_at=now,
            updated_at=now,
        )
        db.add(connector)
        db.flush()
        db.add(AdminAuditEvent(
            actor_user_id=principal.user_id,
            action="SOURCE_CREATED",
            resource_type="source",
            resource_id=source.id,
            outcome="SUCCESS",
            details={
                "source_type": source.source_type,
                "connector_id": str(connector.id),
                "connector_type": connector.connector_type,
                "connector_version": connector.connector_version,
            },
            request_id=request.headers.get("x-request-id"),
            created_at=now,
        ))
        db.commit()
        db.refresh(source)
        db.refresh(connector)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Source registry entry could not be created; retry.") from exc
    return {"data": _source_registry_response(source, [connector])}


@router.patch("/sources/{source_id}/permission-review", response_model=dict)
def update_source_permission_review(
    source_id: UUID,
    payload: SourcePermissionReviewRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Record the administrator's explicit terms/permission decision for automated acquisition."""
    _require_admin(db, principal)
    source = db.scalar(select(Source).where(Source.id == source_id).with_for_update())
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    if payload.status == "APPROVED" and not payload.reference:
        raise HTTPException(
            status_code=422,
            detail="An approved permission review requires an auditable reference.",
        )

    now = datetime.now(timezone.utc)
    previous = {
        "status": source.permission_review_status,
        "reference": source.permission_review_reference,
        "reviewed_at": source.permission_reviewed_at.isoformat() if source.permission_reviewed_at else None,
    }
    source.permission_review_status = payload.status
    source.permission_review_reference = payload.reference
    source.permission_reviewed_at = now if payload.status == "APPROVED" else None
    if payload.status == "APPROVED":
        if source.operational_state == "RESTRICTED" and source.last_error_category == "PERMISSION_REVIEW_REQUIRED":
            source.operational_state = "UNAVAILABLE"
            source.last_error_category = None
    else:
        source.operational_state = "RESTRICTED"
        source.last_error_category = "PERMISSION_REVIEW_REQUIRED"
    source.updated_at = now
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="SOURCE_PERMISSION_REVIEW_CHANGED",
        resource_type="source",
        resource_id=source.id,
        outcome="SUCCESS",
        details={
            "previous": previous,
            "status": source.permission_review_status,
            "reference": source.permission_review_reference,
            "reviewed_at": source.permission_reviewed_at.isoformat() if source.permission_reviewed_at else None,
            "operational_state": source.operational_state,
        },
        request_id=request.headers.get("x-request-id"),
        created_at=now,
    ))
    db.commit()
    db.refresh(source)
    connectors = db.scalars(select(SourceConnector).where(
        SourceConnector.source_id == source.id
    ).order_by(SourceConnector.created_at, SourceConnector.id)).all()
    return {"data": _source_registry_response(source, connectors)}


@router.patch("/sources/{source_id}/status", response_model=dict)
def update_source_registry_status(
    source_id: UUID,
    payload: SourceStatusRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal)
    source = db.scalar(select(Source).where(Source.id == source_id).with_for_update())
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    previous_status = source.status
    if previous_status == payload.status:
        raise HTTPException(status_code=409, detail="Source already has the requested status.")
    source.status = payload.status
    source.updated_at = datetime.now(timezone.utc)
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="SOURCE_STATUS_CHANGED",
        resource_type="source",
        resource_id=source.id,
        outcome="SUCCESS",
        details={"previous_status": previous_status, "status": source.status},
        request_id=request.headers.get("x-request-id"),
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()
    db.refresh(source)
    connectors = db.scalars(select(SourceConnector).where(
        SourceConnector.source_id == source.id
    ).order_by(SourceConnector.created_at, SourceConnector.id)).all()
    return {"data": _source_registry_response(source, connectors)}


@router.post(
    "/sources/{source_id}/connectors/{connector_id}/health-check",
    response_model=dict,
)
def check_source_connector_health(
    source_id: UUID,
    connector_id: UUID,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Check a configured connector and record a sanitized, auditable health result."""
    _require_admin(db, principal)
    source = db.get(Source, source_id)
    connector_row = db.get(SourceConnector, connector_id)
    if source is None or connector_row is None or connector_row.source_id != source_id:
        raise HTTPException(status_code=404, detail="Source connector not found.")
    _enforce_source_permission_review(source, principal, request, db)
    if connector_row.connector_type != GreenhouseJobBoardConnector.connector_type:
        raise HTTPException(status_code=422, detail="Unsupported source connector type.")

    now = datetime.now(timezone.utc)
    context = connector_row.configuration if isinstance(connector_row.configuration, dict) else {}
    try:
        health = GreenhouseJobBoardConnector().health_check(context=context)
    except (GreenhouseConnectorError, ValueError) as exc:
        source.last_observed_at = now
        source.operational_state = "DEGRADED"
        source.last_error_category = "CONNECTOR_HEALTH_CHECK_FAILED"
        source.updated_at = now
        connector_row.status = "DEGRADED"
        db.add(AdminAuditEvent(
            actor_user_id=principal.user_id,
            action="SOURCE_CONNECTOR_HEALTH_CHECK_FAILED",
            resource_type="source_connector",
            resource_id=connector_row.id,
            outcome="FAILED",
            details={
                "source_id": str(source.id),
                "connector_type": connector_row.connector_type,
                "error_code": str(exc) if isinstance(exc, GreenhouseConnectorError) else "invalid_connector_configuration",
                "status": connector_row.status,
            },
            request_id=request.headers.get("x-request-id"),
            created_at=now,
        ))
        db.commit()
        raise HTTPException(
            status_code=502,
            detail={"code": "SOURCE_HEALTH_CHECK_FAILED", "message": "The source connector health check failed."},
        ) from exc

    source.last_observed_at = now
    source.operational_state = "AVAILABLE"
    source.last_error_category = None
    source.updated_at = now
    connector_row.status = "ACTIVE"
    connector_row.last_verified_at = now
    connector_row.updated_at = now
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="SOURCE_CONNECTOR_HEALTH_CHECKED",
        resource_type="source_connector",
        resource_id=connector_row.id,
        outcome="SUCCESS",
        details={
            "source_id": str(source.id),
            "connector_type": connector_row.connector_type,
            "board_name": health["board_name"],
            "job_count": health["job_count"],
            "status": connector_row.status,
        },
        request_id=request.headers.get("x-request-id"),
        created_at=now,
    ))
    db.commit()
    db.refresh(connector_row)
    return {"data": {
        "source_id": str(source.id),
        "connector_id": str(connector_row.id),
        "status": connector_row.status,
        "last_verified_at": connector_row.last_verified_at,
        "source_last_observed_at": source.last_observed_at,
        "board_name": health["board_name"],
        "job_count": health["job_count"],
    }}


def _acquisition_processing_summary(db: Session, run_id: UUID) -> dict[str, int]:
    """Summarize durable downstream events created by this acquisition run.

    This is event-dispatch telemetry, not a claim that jobs were qualified or
    evaluated. Duplicate records have no event in this run and are not counted.
    """
    rows = db.execute(
        select(OutboxEvent.status, func.count(OutboxEvent.id))
        .join(RawSourceRecord, OutboxEvent.aggregate_id == RawSourceRecord.id)
        .where(
            OutboxEvent.event_type == "RAW_SOURCE_RECORD_ACQUIRED",
            RawSourceRecord.acquisition_run_id == run_id,
        )
        .group_by(OutboxEvent.status)
    ).all()
    counts = {str(event_status).upper(): int(count) for event_status, count in rows}
    tracked = sum(counts.values())
    pending = counts.get("PENDING", 0)
    processing = counts.get("PROCESSING", 0)
    processed = counts.get("PROCESSED", 0)
    failed = counts.get("DEAD", 0) + counts.get("FAILED", 0)
    return {
        "tracked_count": tracked,
        "pending_count": pending,
        "processing_count": processing,
        "processed_count": processed,
        "failed_count": failed,
        "other_count": max(tracked - pending - processing - processed - failed, 0),
    }


@router.post("/sources/{source_id}/acquisition-runs", response_model=dict)
def run_source_acquisition(
    source_id: UUID,
    payload: AcquisitionRunRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Run a configured public Greenhouse board through the durable acquisition pipeline."""
    _require_admin(db, principal)
    source = db.get(Source, source_id)
    connector_row = db.get(SourceConnector, payload.connector_id)
    if source is None or connector_row is None or connector_row.source_id != source_id:
        raise HTTPException(status_code=404, detail="Source connector not found.")
    if source.status != "ACTIVE" or connector_row.status != "ACTIVE":
        raise HTTPException(status_code=409, detail="Source and connector must both be active.")
    _enforce_source_permission_review(source, principal, request, db)
    if connector_row.connector_type != GreenhouseJobBoardConnector.connector_type:
        raise HTTPException(status_code=422, detail="Unsupported source connector type.")
    context = connector_row.configuration if isinstance(connector_row.configuration, dict) else {}
    try:
        GreenhouseJobBoardConnector.validate_context(context)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Greenhouse connector configuration is invalid.") from exc

    repository = SqlAlchemyAcquisitionRepository(db)
    existing_run = repository.get_run(source_id, payload.run_key)
    service = AcquisitionService(
        repository,
        GreenhouseJobBoardConnector(),
    )
    try:
        result = service.run(
            source_id,
            connector_row.id,
            payload.run_key,
            context,
        )
    except GreenhouseConnectorError as exc:
        failed_run = SqlAlchemyAcquisitionRepository(db).get_run(source_id, payload.run_key)
        if failed_run is not None:
            db.add(AdminAuditEvent(
                actor_user_id=principal.user_id,
                action="SOURCE_ACQUISITION_FAILED",
                resource_type="acquisition_run",
                resource_id=failed_run.id,
                outcome="FAILED",
                details={
                    "source_id": str(source_id),
                    "connector_id": str(connector_row.id),
                    "run_key": payload.run_key,
                    "error_code": str(exc),
                },
                request_id=request.headers.get("x-request-id"),
                created_at=datetime.now(timezone.utc),
            ))
            db.commit()
        raise HTTPException(
            status_code=502,
            detail={"code": "SOURCE_ACQUISITION_FAILED", "message": "The configured source could not be acquired."},
        ) from exc

    downstream_processing = _acquisition_processing_summary(db, result.run_id)
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action=(
            "SOURCE_ACQUISITION_REPLAYED"
            if existing_run is not None
            else "SOURCE_ACQUISITION_COMPLETED"
        ),
        resource_type="acquisition_run",
        resource_id=result.run_id,
        outcome=result.status,
        details={
            "source_id": str(source_id),
            "connector_id": str(connector_row.id),
            "run_key": payload.run_key,
            "acquired_count": result.acquired_count,
            "persisted_count": result.persisted_count,
            "duplicate_count": result.duplicate_count,
            "downstream_processing": downstream_processing,
        },
        request_id=request.headers.get("x-request-id"),
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()
    return {"data": {
        "run_id": str(result.run_id),
        "source_id": str(source_id),
        "connector_id": str(connector_row.id),
        "status": result.status,
        "acquired_count": result.acquired_count,
        "persisted_count": result.persisted_count,
        "duplicate_count": result.duplicate_count,
        "downstream_processing": downstream_processing,
    }}

@router.get(
    "/sources/{source_id}/acquisition-runs",
    response_model=dict,
)
def list_source_acquisition_runs(
    source_id: UUID,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """List a source's persisted acquisition history with bounded pagination."""
    _require_admin(db, principal)
    if db.get(Source, source_id) is None:
        raise HTTPException(status_code=404, detail="Source not found.")

    total = int(db.scalar(
        select(func.count(AcquisitionRun.id)).where(AcquisitionRun.source_id == source_id)
    ) or 0)
    runs = db.scalars(
        select(AcquisitionRun)
        .where(AcquisitionRun.source_id == source_id)
        .order_by(
            AcquisitionRun.started_at.desc().nulls_last(),
            AcquisitionRun.created_at.desc(),
            AcquisitionRun.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    ).all()

    data = []
    for run in runs:
        metrics = run.metrics if isinstance(run.metrics, dict) else {}
        acquired_count = int(metrics.get("acquired_count", 0))
        persisted_count = int(metrics.get("persisted_count", 0))
        data.append({
            "run_id": str(run.id),
            "run_key": run.run_key,
            "connector_id": str(run.source_connector_id),
            "status": run.status,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "acquired_count": acquired_count,
            "persisted_count": persisted_count,
            "duplicate_count": max(acquired_count - persisted_count, 0),
            "error_summary": run.error_summary,
        })
    return {"data": data, "pagination": {"limit": limit, "offset": offset, "total": total}}


@router.get(
    "/sources/{source_id}/acquisition-runs/{run_id}",
    response_model=dict,
)
def get_source_acquisition_run(
    source_id: UUID,
    run_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return persisted acquisition state and a fresh downstream outbox summary."""
    _require_admin(db, principal)
    run = db.scalar(
        select(AcquisitionRun).where(
            AcquisitionRun.id == run_id,
            AcquisitionRun.source_id == source_id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Acquisition run not found.")

    metrics = run.metrics if isinstance(run.metrics, dict) else {}
    acquired_count = int(metrics.get("acquired_count", 0))
    persisted_count = int(metrics.get("persisted_count", 0))
    return {"data": {
        "run_id": str(run.id),
        "source_id": str(run.source_id),
        "connector_id": str(run.source_connector_id),
        "status": run.status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "acquired_count": acquired_count,
        "persisted_count": persisted_count,
        "duplicate_count": max(acquired_count - persisted_count, 0),
        "error_summary": run.error_summary,
        "downstream_processing": _acquisition_processing_summary(db, run.id),
    }}

