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
from app.infrastructure.db.models.governance import AdminAuditEvent, AdminGrant, GovernanceConfigVersion
from app.infrastructure.db.models.identity import User
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/admin", tags=["admin-governance"])
SECRET_KEYS = {"apikey", "secret", "password", "accesstoken", "refreshtoken", "credential", "credentials"}

class ConfigDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_key: str = Field(min_length=4, max_length=160, pattern=r"^(ai\.task|system)\.[a-z0-9_.-]{1,140}$")
    payload: dict[str, Any] = Field(min_length=1)
    supersedes_id: UUID | None = None

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
            if normalized in SECRET_KEYS:
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
