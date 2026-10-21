"""Protected system-governance endpoints. Admin grants are operator-managed."""
from __future__ import annotations
import hashlib
import json
import os
import re
import smtplib
import ssl
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.deps import get_principal
from app.application.ai.task_registry import AI_TASK_REGISTRY
from app.application.opportunities.evaluation_policy import evaluate_candidate_job, validate_decision_rules
from app.infrastructure.db.models.governance import AIExecution, AdminAuditEvent, AdminGrant, AdminImpersonationSession, AuditEvent, GovernanceConfigVersion
from app.application.jobs.service import AcquisitionService
from app.application.jobs.registered_acquisition import build_acquisition_policy_revalidator
from app.application.jobs.discovery_missions import enqueue_discovery_completion_notification
from app.application.jobs.identity_resolution_review import JobIdentityResolutionReviewService
from app.application.jobs.source_governance import (
    assert_automated_acquisition_permitted,
    classify_acquisition_outcome,
    normalize_public_target_url,
)
from app.application.jobs.target_policy import (
    ForbiddenTargetError,
    assert_target_not_forbidden,
    normalize_forbidden_rule,
)
from app.application.jobs.normalization import normalize_application_route_type
from app.infrastructure.connectors.greenhouse import GreenhouseConnectorError, GreenhouseJobBoardConnector
from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry, UnsupportedConnectorError
from app.infrastructure.db.models.source import Source, SourceConnector, SourceConnectorConfigurationVersion
from app.infrastructure.db.models.work_item import WorkItem
from app.infrastructure.db.models.target_policy import ForbiddenTargetRule
from app.infrastructure.db.models.discovery_target import DiscoveryTarget, DiscoveryTargetEvent
from app.infrastructure.db.models.integration_health import IntegrationHealthObservation
from app.infrastructure.db.models.worker_heartbeat import WorkerHeartbeat
from app.application.operations.worker_heartbeat import safe_worker_metrics
from app.infrastructure.db.models import DiscoveryMission
from app.infrastructure.db.models.acquisition import AcquisitionRun, RawSourceRecord
from app.infrastructure.db.models.jobs import JobDiscoveryEvent
from app.infrastructure.db.models.outbox import OutboxEvent
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
from app.infrastructure.db.models.identity import User
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/admin", tags=["admin-governance"])
SECRET_KEYS = {"apikey", "secret", "secretkey", "password", "token", "accesstoken", "refreshtoken", "credential", "credentials", "privatekey", "signingkey", "authorization", "bearer"}

class ConfigDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_key: str = Field(min_length=4, max_length=160, pattern=r"^(?:ai\.task\.[a-z0-9_.-]{1,140}|decision\.rules\.candidate_evaluation)$")
    payload: dict[str, Any] = Field(min_length=1)
    supersedes_id: UUID | None = None

class EmailDeliveryRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_must_be_nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A nonblank retry reason is required.")
        return normalized


class ForbiddenTargetRuleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_type: Literal["HOST_EXACT", "HOST_SUFFIX", "URL_EXACT", "URL_PATH_PREFIX"]
    pattern: str = Field(min_length=1, max_length=2048)
    connector_scope: Literal[
        "ALL", "SCRAPY", "PLAYWRIGHT", "JSON_API", "RSS_ATOM", "JOBSPY", "GREENHOUSE_JOB_BOARD"
    ] = "ALL"
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("pattern", "reason")
    @classmethod
    def text_must_be_nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be blank.")
        return normalized


class ForbiddenTargetRuleStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_must_be_nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A nonblank change reason is required.")
        return normalized


class DiscoveryTargetCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: UUID
    source_connector_id: UUID
    url: str = Field(min_length=1, max_length=2048)
    target_kind: Literal["CRAWL", "SEARCH"]
    purpose: str = Field(min_length=1, max_length=64)

    @field_validator("purpose")
    @classmethod
    def purpose_must_be_nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Target purpose must not be blank.")
        return normalized


class DiscoveryTargetStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ACTIVE", "PAUSED", "RETIRED"]
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_must_be_nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A nonblank lifecycle-change reason is required.")
        return normalized


class ImpersonationSessionStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_user_id: UUID
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_must_be_nonblank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A nonblank impersonation reason is required.")
        return normalized


class DecisionRulePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: dict[str, Any] = Field(min_length=1)
    job: dict[str, Any] = Field(min_length=1)
    job_description: str = Field(default="", max_length=70_000)
    description_version_id: str | None = Field(default=None, max_length=128)
    description_completeness: str | None = Field(default=None, max_length=32)
    candidate_evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    preferences: dict[str, Any] = Field(default_factory=dict)
    semantic_matches: list[dict[str, Any]] | None = Field(default=None, max_length=500)



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
    connector_type: str = Field(default="GREENHOUSE_JOB_BOARD", min_length=2, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    connector_version: str | None = Field(default=None, min_length=1, max_length=64)
    configuration: dict[str, Any] = Field(default_factory=dict)
    # Backwards-compatible convenience for the original Greenhouse-only setup flow.
    board_token: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    application_route_type: str | None = Field(default=None, max_length=64)

    @field_validator("connector_type")
    @classmethod
    def connector_type_must_be_supported(cls, value: str) -> str:
        normalized = value.strip().upper()
        supported = {kind for kind, _version in AcquisitionConnectorRegistry().supported_connectors}
        if normalized not in supported:
            raise ValueError("Connector type is not registered.")
        return normalized

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Source name must not be blank.")
        return value.strip()

    @field_validator("application_route_type")
    @classmethod
    def route_type_must_be_supported(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = normalize_application_route_type(value)
        if normalized is None:
            raise ValueError("Application route type must be a recognized explicit classification.")
        return normalized

class SourceConnectorConfigurationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    configuration: dict[str, Any] = Field(min_length=1)
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A nonblank configuration-change reason is required.")
        return value.strip()


class OperationalRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=100, ge=1, le=500)
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("An operational recovery reason is required.")
        return value.strip()


class OutboxRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("An outbox retry reason is required.")
        return value.strip()

class WorkItemActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A work-item action reason is required.")
        return value.strip()


class SourceConnectorConfigurationActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A nonblank configuration-activation reason is required.")
        return value.strip()


class SourceStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(pattern=r"^(ACTIVE|PAUSED|DISABLED)$")

class JobIdentityResolutionDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["MATCH_EXISTING", "CREATE_NEW"]
    job_id: UUID | None = None
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A nonblank identity-resolution reason is required.")
        return value.strip()

    @model_validator(mode="after")
    def target_matches_action(self):
        if self.action == "MATCH_EXISTING" and self.job_id is None:
            raise ValueError("job_id is required when action is MATCH_EXISTING.")
        if self.action == "CREATE_NEW" and self.job_id is not None:
            raise ValueError("job_id must be omitted when action is CREATE_NEW.")
        return self


class SourcePermissionReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(pattern=r"^(APPROVED|PENDING|REJECTED)$")
    reference: str | None = Field(default=None, max_length=2000)
    access_method: str | None = Field(default=None, max_length=128)
    terms_reference: str | None = Field(default=None, max_length=2000)
    rate_limit_policy: str | None = Field(default=None, max_length=1000)
    rate_limit_reference: str | None = Field(default=None, max_length=2000)
    review_notes: str | None = Field(default=None, max_length=2000)

    @field_validator(
        "reference", "access_method", "terms_reference", "rate_limit_policy",
        "rate_limit_reference", "review_notes",
    )
    @classmethod
    def review_fields_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Permission review fields must not be blank.")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def approved_review_requires_method_terms_and_rate_limit_policy(self):
        if self.status == "APPROVED":
            required = {
                "access_method": self.access_method,
                "terms_reference": self.terms_reference,
                "rate_limit_policy": self.rate_limit_policy,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError(
                    "An approved permission review requires access method, terms reference, "
                    "and rate-limit policy."
                )
        return self

class UserStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_status: Literal["ACTIVE", "SUSPENDED", "DEACTIVATED"]
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A nonblank reason is required.")
        return value.strip()


class UserStatusResponse(BaseModel):
    id: UUID
    status: str
    revision: int
    display_name: str | None
    email: str | None
    email_verified: bool
    photo_url: str | None
    onboarding_status: str
    last_login_at: datetime | None
    updated_at: datetime


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
    input_contract_version: str
    input_fingerprint: str
    output_contract_version: str
    config_version_id: UUID | None
    config_fingerprint: str
    contract_snapshot: dict[str, Any]
    output_fingerprint: str | None
    provider: str
    model: str
    status: str
    fallback_attempted: bool
    fallback_used: bool
    latency_ms: int | None
    error_code: str | None
    validation_result: dict[str, Any]
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
    if config_key == "decision.rules.candidate_evaluation":
        try:
            validate_decision_rules(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return
    if not config_key.startswith("ai.task."):
        raise HTTPException(status_code=422, detail="Unsupported configuration namespace.")
    task_id = config_key.removeprefix("ai.task.")
    if task_id not in AI_TASK_REGISTRY:
        raise HTTPException(status_code=422, detail="Unknown AI task configuration key.")
    allowed = {"system_instruction", "model", "provider", "temperature", "fallback_provider", "fallback_model"}
    if set(payload) - allowed:
        raise HTTPException(status_code=422, detail="AI task configs contain unsupported fields.")
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
    fallback_provider = payload.get("fallback_provider")
    fallback_model = payload.get("fallback_model")
    if (fallback_provider is None) != (fallback_model is None):
        raise HTTPException(status_code=422, detail="fallback_provider and fallback_model must be configured together.")
    if fallback_provider is not None:
        if not isinstance(fallback_provider, str) or fallback_provider not in {"google", "openai"}:
            raise HTTPException(status_code=422, detail="fallback_provider must be google or openai.")
        fallback_pattern = r"gemini-[A-Za-z0-9._-]{1,100}" if fallback_provider == "google" else r"gpt-[A-Za-z0-9._-]{1,100}"
        if not isinstance(fallback_model, str) or len(fallback_model) > 110 or not re.fullmatch(fallback_pattern, fallback_model):
            raise HTTPException(status_code=422, detail="fallback_model must be valid for fallback_provider.")
        if provider is None:
            raise HTTPException(status_code=422, detail="Primary provider must be explicit when fallback is configured.")
        if provider == fallback_provider:
            raise HTTPException(status_code=422, detail="Fallback must use a different provider from the primary route.")

def _require_admin(
    db: Session,
    principal: AuthenticatedPrincipal,
    request: Request | None = None,
) -> None:
    grant = db.scalar(select(AdminGrant).join(User, User.id == AdminGrant.user_id).where(
        AdminGrant.user_id == principal.user_id, AdminGrant.role == "ADMIN",
        AdminGrant.active.is_(True), User.status == "ACTIVE"))
    if grant is None:
        # Audit denials for real persisted accounts. Unknown/spoofed identities cannot satisfy
        # the actor foreign key and therefore do not create fabricated audit actors.
        actor_exists = hasattr(db, "get") and db.get(User, principal.user_id) is not None
        if request is not None and actor_exists:
            db.add(AdminAuditEvent(
                actor_user_id=principal.user_id,
                action="ADMIN_ACCESS_DENIED",
                resource_type="admin_route",
                resource_id=None,
                outcome="DENIED",
                details={
                    "method": request.method,
                    "path": request.url.path,
                    "reason": "ADMIN_GRANT_REQUIRED",
                },
                request_id=request.headers.get("x-request-id"),
                created_at=datetime.now(timezone.utc),
            ))
            try:
                db.commit()
            except Exception as exc:
                db.rollback()
                raise HTTPException(
                    status_code=500,
                    detail="Administrative access denial could not be recorded.",
                ) from exc
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
    _require_admin(db, principal, request)
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
        "input_contract_version": row.input_contract_version,
        "input_fingerprint": row.input_fingerprint,
        "output_contract_version": row.output_contract_version,
        "config_version_id": row.config_version_id,
        "config_fingerprint": row.config_fingerprint,
        "contract_snapshot": row.contract_snapshot,
        "output_fingerprint": row.output_fingerprint,
        "provider": row.provider,
        "model": row.model, "status": row.status,
        "fallback_attempted": row.fallback_attempted,
        "fallback_used": row.fallback_used,
        "latency_ms": row.latency_ms,
        "error_code": row.error_code, "validation_result": row.validation_result,
        "request_id": row.request_id,
        "created_at": row.created_at, "finished_at": row.finished_at,
    } for row in rows]

@router.get("/audit", response_model=list[AuditEventResponse])
def list_admin_audit_events(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    actor_user_id: UUID | None = None,
    action: str | None = Query(default=None, min_length=1, max_length=100),
    resource_type: str | None = Query(default=None, min_length=1, max_length=100),
    resource_id: UUID | None = None,
    outcome: str | None = Query(default=None, min_length=1, max_length=24),
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Query immutable audit history with bounded pagination and allowlisted fields."""
    _require_admin(db, principal, request)
    if action is not None and not action.strip():
        raise HTTPException(status_code=422, detail="Action filter must not be blank.")
    if resource_type is not None and not resource_type.strip():
        raise HTTPException(status_code=422, detail="Resource type filter must not be blank.")
    if outcome is not None and not outcome.strip():
        raise HTTPException(status_code=422, detail="Outcome filter must not be blank.")

    # Treat timezone-less query bounds as UTC rather than comparing naive and aware values.
    if created_after is not None and created_after.tzinfo is None:
        created_after = created_after.replace(tzinfo=timezone.utc)
    if created_before is not None and created_before.tzinfo is None:
        created_before = created_before.replace(tzinfo=timezone.utc)
    if created_after is not None and created_before is not None and created_after > created_before:
        raise HTTPException(
            status_code=422,
            detail="created_after must be less than or equal to created_before.",
        )

    query = select(AdminAuditEvent)
    if actor_user_id is not None:
        query = query.where(AdminAuditEvent.actor_user_id == actor_user_id)
    if action is not None:
        query = query.where(AdminAuditEvent.action == action.strip())
    if resource_type is not None:
        query = query.where(AdminAuditEvent.resource_type == resource_type.strip())
    if resource_id is not None:
        query = query.where(AdminAuditEvent.resource_id == resource_id)
    if outcome is not None:
        query = query.where(AdminAuditEvent.outcome == outcome.strip().upper())
    if created_after is not None:
        query = query.where(AdminAuditEvent.created_at >= created_after)
    if created_before is not None:
        query = query.where(AdminAuditEvent.created_at <= created_before)

    rows = db.scalars(query.order_by(
        AdminAuditEvent.created_at.desc(), AdminAuditEvent.id.desc()
    ).limit(limit).offset(offset)).all()
    return [{
        "id": row.id, "actor_user_id": row.actor_user_id, "action": row.action,
        "resource_type": row.resource_type, "resource_id": row.resource_id,
        "outcome": row.outcome, "details": row.details, "request_id": row.request_id,
        "created_at": row.created_at,
    } for row in rows]


@router.patch("/users/{user_id}/status", response_model=UserStatusResponse)
def update_user_status(
    user_id: UUID,
    payload: UserStatusRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Change account status with optimistic concurrency and an atomic audit event."""
    _require_admin(db, principal, request)
    def record_rejected_attempt(
        *, outcome: str, validation_result: str, target_revision: int | None = None,
        previous_status: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        details = {
            "requested_status": payload.target_status,
            "expected_revision": payload.expected_revision,
            "reason": payload.reason,
            "validation_result": validation_result,
        }
        if target_revision is not None:
            details["current_revision"] = target_revision
        if previous_status is not None:
            details["previous_status"] = previous_status
        db.add(AdminAuditEvent(
            actor_user_id=principal.user_id,
            action="USER_STATUS_CHANGE_REJECTED",
            resource_type="user",
            resource_id=user_id,
            outcome=outcome,
            details=details,
            request_id=request.headers.get("x-request-id"),
            created_at=now,
        ))
        db.commit()

    target = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if target is None:
        record_rejected_attempt(outcome="NOT_FOUND", validation_result="TARGET_NOT_FOUND")
        raise HTTPException(status_code=404, detail="Target user not found.")
    if target.revision != payload.expected_revision:
        record_rejected_attempt(
            outcome="CONFLICT",
            validation_result="STALE_REVISION",
            target_revision=target.revision,
            previous_status=target.status,
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_REVISION",
                "message": "The account changed. Reload it before applying another status change.",
            },
        )
    if target.status not in {"ACTIVE", "SUSPENDED", "DEACTIVATED"}:
        record_rejected_attempt(
            outcome="REJECTED",
            validation_result="ACCOUNT_STATUS_INVALID",
            target_revision=target.revision,
            previous_status=target.status,
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "ACCOUNT_STATUS_INVALID", "message": "The current account status is not supported."},
        )
    if target.status == payload.target_status:
        record_rejected_attempt(
            outcome="REJECTED",
            validation_result="NO_STATUS_CHANGE",
            target_revision=target.revision,
            previous_status=target.status,
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "NO_STATUS_CHANGE", "message": "The account already has the requested status."},
        )

    previous_status = target.status
    previous_revision = target.revision
    now = datetime.now(timezone.utc)
    target.status = payload.target_status
    target.revision += 1
    target.updated_at = now
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="USER_STATUS_CHANGED",
        resource_type="user",
        resource_id=target.id,
        outcome="SUCCESS",
        details={
            "previous_status": previous_status,
            "new_status": target.status,
            "before_state": {"status": previous_status, "revision": previous_revision},
            "after_state": {"status": target.status, "revision": target.revision},
            "reason": payload.reason,
            "validation_result": "VALIDATED",
            "revision": target.revision,
        },
        request_id=request.headers.get("x-request-id"),
        created_at=now,
    ))
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        # A persistence failure can only be audited if the database is still writable;
        # never replace the original error or store raw database/driver text.
        try:
            db.add(AdminAuditEvent(
                actor_user_id=principal.user_id,
                action="USER_STATUS_CHANGE_FAILED",
                resource_type="user",
                resource_id=user_id,
                outcome="FAILED",
                details={
                    "requested_status": payload.target_status,
                    "expected_revision": payload.expected_revision,
                    "reason": payload.reason,
                    "validation_result": "PERSISTENCE_FAILED",
                    "error_type": type(exc).__name__,
                },
                request_id=request.headers.get("x-request-id"),
                created_at=datetime.now(timezone.utc),
            ))
            db.commit()
        except Exception:
            db.rollback()
        raise

    return {
        "id": target.id,
        "status": target.status,
        "revision": target.revision,
        "display_name": target.display_name,
        "email": target.email,
        "email_verified": target.email_verified,
        "photo_url": target.photo_url,
        "onboarding_status": target.onboarding_status,
        "last_login_at": target.last_login_at,
        "updated_at": target.updated_at,
    }


@router.get("/configs", response_model=list[ConfigVersionResponse])
def list_config_versions(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    config_key: str | None = Query(default=None, min_length=1, max_length=160),
    config_status: Literal["DRAFT", "PUBLISHED", "RETIRED"] | None = Query(default=None, alias="status"),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    _require_admin(db, principal, request)
    query = select(GovernanceConfigVersion)
    if config_key is not None:
        query = query.where(GovernanceConfigVersion.config_key == config_key.strip())
    if config_status is not None:
        query = query.where(GovernanceConfigVersion.status == config_status)
    rows = db.scalars(
        query.order_by(
            GovernanceConfigVersion.config_key.asc(),
            GovernanceConfigVersion.version.desc(),
            GovernanceConfigVersion.id.desc(),
        ).limit(limit).offset(offset)
    ).all()
    return [_as_dict(row) for row in rows]

@router.post("/configs", response_model=ConfigVersionResponse, status_code=status.HTTP_201_CREATED)
def create_config_draft(payload: ConfigDraftRequest, request: Request,
                         principal: AuthenticatedPrincipal = Depends(get_principal),
                         db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_admin(db, principal, request)
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
    _require_admin(db, principal, request)
    row = db.scalar(select(GovernanceConfigVersion).where(
        GovernanceConfigVersion.id == config_id).with_for_update())
    if row is None:
        raise HTTPException(status_code=404, detail="Configuration version not found.")
    if row.status != "DRAFT":
        raise HTTPException(status_code=409, detail="Only draft configuration versions can be published.")
    _reject_secrets(row.payload)
    _validate_config_payload(row.config_key, row.payload)
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
    _require_admin(db, principal, request)
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


@router.post("/decision-rules/preview")
def preview_decision_rules(
    request: Request,
    payload: DecisionRulePreviewRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Evaluate supplied test inputs against draft thresholds without persisting candidate state."""
    _require_admin(db, principal, request)
    _reject_secrets(payload.rules)
    _validate_config_payload("decision.rules.candidate_evaluation", payload.rules)
    try:
        result = evaluate_candidate_job(
            job=payload.job,
            job_description=payload.job_description,
            description_version_id=payload.description_version_id,
            description_completeness=payload.description_completeness,
            candidate_evidence=payload.candidate_evidence,
            preferences=payload.preferences,
            semantic_matches=payload.semantic_matches,
            decision_rules=payload.rules,
            policy_version_override="candidate-evaluation-preview",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "preview_only": True,
        "rules": payload.rules,
        "result": result,
    }


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
    _require_admin(db, principal, request)
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
    _require_admin(db, principal, request)
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
    _require_admin(db, principal, request)
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


def _configuration_fingerprint(configuration: dict[str, Any]) -> str:
    canonical = json.dumps(configuration, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _redact_connector_configuration(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            is_secret = (
                normalized in SECRET_KEYS
                or normalized.endswith(("token", "secret", "password", "credential", "privatekey", "signingkey", "authorization", "bearer"))
            )
            redacted[key] = "[REDACTED]" if is_secret else _redact_connector_configuration(item)
        return redacted
    if isinstance(value, list):
        return [_redact_connector_configuration(item) for item in value]
    return value


def _validate_connector_configuration(connector_type: str, configuration: dict[str, Any]) -> None:
    supported = dict(AcquisitionConnectorRegistry().supported_connectors)
    if connector_type not in supported:
        raise HTTPException(status_code=422, detail="Connector type is not registered.")
    required_fields = {
        "JOBSPY": ("search_terms", "location"),
        "SCRAPY": ("start_urls",),
        "PLAYWRIGHT": ("start_urls",),
        "JSON_API": ("endpoint_url",),
        "RSS_ATOM": ("feed_url",),
    }
    missing = [
        field for field in required_fields.get(connector_type, ())
        if field not in configuration or configuration[field] in (None, "", [])
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_CONNECTOR_CONFIGURATION", "missing_fields": missing},
        )
    if connector_type == GreenhouseJobBoardConnector.connector_type:
        try:
            GreenhouseJobBoardConnector.validate_context(configuration)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Greenhouse board configuration is invalid.") from exc
    else:
        try:
            AcquisitionConnectorRegistry().create(connector_type, supported[connector_type])
        except UnsupportedConnectorError as exc:
            raise HTTPException(status_code=422, detail="Connector type or version is not supported.") from exc
        if connector_type == "JOBSPY" and (
            not isinstance(configuration.get("search_terms"), list)
            or any(not isinstance(term, str) or not term.strip() for term in configuration["search_terms"])
            or not isinstance(configuration.get("location"), str)
            or not configuration["location"].strip()
        ):
            raise HTTPException(status_code=422, detail="JobSpy requires non-empty search_terms and location.")
        if connector_type in {"SCRAPY", "PLAYWRIGHT"}:
            start_urls = configuration.get("start_urls")
            if (
                not isinstance(start_urls, list)
                or not start_urls
                or any(not isinstance(url, str) or not url.strip() for url in start_urls)
            ):
                raise HTTPException(status_code=422, detail="Scrapy and Playwright require non-empty start_urls.")
            try:
                configuration["start_urls"] = [
                    normalize_public_target_url(url) for url in start_urls
                ]
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "UNSAFE_SOURCE_TARGET_URL", "message": str(exc)},
                ) from exc

        url_key = {"JSON_API": "endpoint_url", "RSS_ATOM": "feed_url"}.get(connector_type)
        if url_key:
            if not isinstance(configuration.get(url_key), str):
                raise HTTPException(status_code=422, detail=f"{url_key} must be a URL string.")
            try:
                configuration[url_key] = normalize_public_target_url(
                    configuration[url_key], require_https=True
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "UNSAFE_SOURCE_TARGET_URL", "message": str(exc)},
                ) from exc


def _active_forbidden_target_rule_payloads(db: Session) -> list[dict[str, Any]]:
    return [
        {
            "id": str(rule.id),
            "rule_type": rule.rule_type,
            "pattern": rule.pattern,
            "connector_scope": rule.connector_scope,
            "reason": rule.reason,
            "active": rule.active,
        }
        for rule in db.scalars(
            select(ForbiddenTargetRule)
            .where(ForbiddenTargetRule.active.is_(True))
            .order_by(ForbiddenTargetRule.id)
        ).all()
    ]


def _assert_configuration_target_allowed(
    db: Session, connector_type: str, configuration: dict[str, Any]
) -> None:
    """Reject a forbidden target before configuration can be saved or published."""
    connector = connector_type.strip().upper()
    rules = _active_forbidden_target_rule_payloads(db)
    if connector == "JOBSPY" and any(
        rule.get("active", True)
        and str(rule.get("connector_scope", "ALL")).upper() in {"ALL", "JOBSPY"}
        for rule in rules
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "FORBIDDEN_TARGET_POLICY_UNSUPPORTED",
                "message": "The JOBSPY adapter cannot enforce the active target policy for every outbound request.",
            },
        )

    targets: list[str] = []
    if connector in {"SCRAPY", "PLAYWRIGHT"}:
        values = configuration.get("start_urls")
        if isinstance(values, list):
            targets.extend(value for value in values if isinstance(value, str))
    elif connector == "JSON_API":
        value = configuration.get("endpoint_url")
        if isinstance(value, str):
            targets.append(value)
    elif connector == "RSS_ATOM":
        value = configuration.get("feed_url")
        if isinstance(value, str):
            targets.append(value)
    elif connector == GreenhouseJobBoardConnector.connector_type:
        board_token = configuration.get("board_token")
        if isinstance(board_token, str) and board_token:
            targets.extend([
                f"{GreenhouseJobBoardConnector.BASE_URL}/v1/boards/{board_token}",
                f"{GreenhouseJobBoardConnector.BASE_URL}/v1/boards/{board_token}/jobs?content=true",
            ])

    for target in targets:
        try:
            assert_target_not_forbidden(target, rules, connector_type=connector)
        except ForbiddenTargetError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "FORBIDDEN_TARGET_RULE_MATCHED",
                    "rule_id": exc.rule_id,
                    "message": "The target is blocked by an active forbidden-target rule.",
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INVALID_TARGET_POLICY_INPUT",
                    "message": "The configured target cannot be evaluated by the target policy.",
                },
            ) from exc


def _active_connector_configuration(db: Session, connector: SourceConnector) -> dict[str, Any]:
    version = db.scalar(select(SourceConnectorConfigurationVersion).where(
        SourceConnectorConfigurationVersion.source_connector_id == connector.id,
        SourceConnectorConfigurationVersion.version == connector.active_configuration_version,
        SourceConnectorConfigurationVersion.status == "PUBLISHED",
    ))
    if version is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "SOURCE_CONFIGURATION_VERSION_MISSING", "message": "The active connector configuration version is unavailable."},
        )
    return dict(version.configuration)


def _source_registry_response(source: Source, connectors: list[SourceConnector]) -> dict[str, Any]:
    return {
        "id": source.id,
        "name": source.name,
        "source_type": source.source_type,
        "base_url": source.base_url,
        "application_route_type": normalize_application_route_type(
            (source.configuration or {}).get("application_route_type")
            or (source.configuration or {}).get("application_route_classification")
        ) if isinstance(source.configuration, dict) else None,
        "status": source.status,
        "permission_review_status": source.permission_review_status,
        "permission_review_reference": source.permission_review_reference,
        "permission_reviewed_at": source.permission_reviewed_at,
        "permission_review_details": (
            dict(source.configuration.get("permission_review", {}))
            if isinstance(source.configuration, dict)
            and isinstance(source.configuration.get("permission_review"), dict)
            else {}
        ),
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
            "application_route_type": normalize_application_route_type(
                (connector.configuration or {}).get("application_route_type")
                or (connector.configuration or {}).get("application_route_classification")
            ) if isinstance(connector.configuration, dict) else None,
            "capabilities": {
                **(connector.capabilities or {}),
                "supported_target_kinds": list(AcquisitionConnectorRegistry().supported_target_kinds(
                    connector.connector_type, connector.connector_version
                )),
            },
            "limits": connector.limits or {},
            "last_verified_at": connector.last_verified_at,
            "revision": connector.revision,
            "active_configuration_version": connector.active_configuration_version,
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


@router.get("/job-resolutions", response_model=dict)
def list_job_identity_resolutions(
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal, request)
    return JobIdentityResolutionReviewService(db).list_pending(limit=limit, offset=offset)


@router.get("/job-resolutions/{resolution_id}", response_model=dict)
def get_job_identity_resolution(
    request: Request,
    resolution_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal, request)
    data = JobIdentityResolutionReviewService(db).get_detail(resolution_id)
    data["raw_record"]["raw_content"] = _redact_connector_configuration(
        data["raw_record"]["raw_content"]
    )
    return {"data": data}


@router.post("/job-resolutions/{resolution_id}/resolve", response_model=dict)
def resolve_job_identity_resolution(
    resolution_id: UUID,
    payload: JobIdentityResolutionDecisionRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal, request)
    result = JobIdentityResolutionReviewService(db).resolve(
        resolution_id=resolution_id,
        action=payload.action,
        job_id=payload.job_id,
        reason=payload.reason,
        actor_user_id=principal.user_id,
        request_id=request.headers.get("x-request-id"),
    )
    return {"data": result}


@router.get("/sources", response_model=dict)
def list_source_registry(
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    query: str | None = Query(default=None, min_length=1, max_length=160),
    source_status: Literal["ACTIVE", "PAUSED", "DISABLED"] | None = Query(default=None, alias="status"),
    operational_state: Literal[
        "CONFIGURED", "CHECKED", "HEALTHY", "DEGRADED", "RESTRICTED", "FAILED", "UNAVAILABLE"
    ] | None = None,
    permission_review_status: Literal["APPROVED", "PENDING", "REJECTED"] | None = None,
    connector_type: str | None = Query(default=None, min_length=2, max_length=64),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal, request)
    if query is not None and not query.strip():
        raise HTTPException(status_code=422, detail="Search query must not be blank.")

    filters = []
    if source_status is not None:
        filters.append(Source.status == source_status)
    if operational_state is not None:
        filters.append(Source.operational_state == operational_state)
    if permission_review_status is not None:
        filters.append(Source.permission_review_status == permission_review_status)
    if query is not None:
        pattern = f"%{query.strip()}%"
        filters.append(or_(
            Source.name.ilike(pattern),
            Source.source_type.ilike(pattern),
            Source.base_url.ilike(pattern),
        ))
    if connector_type is not None:
        filters.append(Source.id.in_(
            select(SourceConnector.source_id).where(
                SourceConnector.connector_type == connector_type.strip().upper()
            )
        ))

    count_statement = select(func.count(Source.id))
    statement = select(Source)
    if filters:
        count_statement = count_statement.where(*filters)
        statement = statement.where(*filters)
    total = db.scalar(count_statement) or 0
    sources = db.scalars(
        statement.order_by(Source.created_at.desc(), Source.id.desc())
        .limit(limit).offset(offset)
    ).all()
    source_ids = [source.id for source in sources]
    connectors = db.scalars(
        select(SourceConnector)
        .where(SourceConnector.source_id.in_(source_ids))
        .order_by(SourceConnector.created_at, SourceConnector.id)
    ).all() if source_ids else []
    by_source: dict[UUID, list[SourceConnector]] = {}
    for connector in connectors:
        by_source.setdefault(connector.source_id, []).append(connector)
    return {
        "data": [
            _source_registry_response(source, by_source.get(source.id, []))
            for source in sources
        ],
        "pagination": {"limit": limit, "offset": offset, "total": total},
    }


@router.post("/sources", response_model=dict, status_code=status.HTTP_201_CREATED)
def create_source_registry_entry(
    payload: SourceCreateRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal, request)
    connector_type = payload.connector_type.strip().upper()
    supported = dict((kind, version) for kind, version in AcquisitionConnectorRegistry().supported_connectors)
    connector_version = payload.connector_version or supported.get(connector_type)
    if connector_version is None or (connector_type, connector_version) not in AcquisitionConnectorRegistry().supported_connectors:
        raise HTTPException(status_code=422, detail="Connector type or version is not supported.")

    connector_configuration = dict(payload.configuration)
    if connector_type == GreenhouseJobBoardConnector.connector_type:
        if payload.board_token is not None:
            connector_configuration.setdefault("board_token", payload.board_token)
        try:
            GreenhouseJobBoardConnector.validate_context(connector_configuration)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Greenhouse board token is invalid.") from exc
        base_url = GreenhouseJobBoardConnector.BASE_URL
        capabilities = {
            "published_jobs": True,
            "health_check": True,
            "supported_target_kinds": list(
                AcquisitionConnectorRegistry().supported_target_kinds(connector_type, connector_version)
            ),
        }
        limits = {"timeout_seconds": GreenhouseJobBoardConnector.TIMEOUT_SECONDS}
    else:
        if payload.board_token is not None:
            raise HTTPException(status_code=422, detail="board_token is only supported for Greenhouse connectors.")
        required_fields = {
            "JOBSPY": ("search_terms", "location"),
            "SCRAPY": ("start_urls",),
            "PLAYWRIGHT": ("start_urls",),
            "JSON_API": ("endpoint_url",),
            "RSS_ATOM": ("feed_url",),
        }
        missing = [
            field for field in required_fields.get(connector_type, ())
            if field not in connector_configuration or connector_configuration[field] in (None, "", [])
        ]
        if missing:
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_CONNECTOR_CONFIGURATION", "missing_fields": missing},
            )
        try:
            AcquisitionConnectorRegistry().create(connector_type, connector_version)
        except UnsupportedConnectorError as exc:
            raise HTTPException(status_code=422, detail="Connector type or version is not supported.") from exc
        base_url = (
            connector_configuration.get("endpoint_url")
            or connector_configuration.get("feed_url")
            or (connector_configuration.get("start_urls") or [None])[0]
        )
        capabilities = {
            "published_jobs": True,
            "health_check": "bounded_acquisition_probe",
            "supported_target_kinds": list(
                AcquisitionConnectorRegistry().supported_target_kinds(connector_type, connector_version)
            ),
        }
        limits = {}

    _validate_connector_configuration(connector_type, connector_configuration)
    _assert_configuration_target_allowed(db, connector_type, connector_configuration)
    if connector_type != GreenhouseJobBoardConnector.connector_type:
        base_url = (
            connector_configuration.get("endpoint_url")
            or connector_configuration.get("feed_url")
            or ((connector_configuration.get("start_urls") or [None])[0])
        )
    now = datetime.now(timezone.utc)
    source = Source(
        name=payload.name,
        source_type=connector_type,
        base_url=base_url,
        status="ACTIVE",
        configuration=(
            {"application_route_type": payload.application_route_type}
            if payload.application_route_type is not None else {}
        ),
        created_at=now,
        updated_at=now,
    )
    db.add(source)
    try:
        db.flush()
        connector = SourceConnector(
            source_id=source.id,
            connector_type=connector_type,
            connector_version=connector_version,
            status="ACTIVE",
            configuration=connector_configuration,
            capabilities=capabilities,
            limits=limits,
            created_at=now,
            updated_at=now,
        )
        db.add(connector)
        db.flush()
        db.add(SourceConnectorConfigurationVersion(
            source_connector_id=connector.id,
            version=1,
            configuration=connector_configuration,
            configuration_fingerprint=_configuration_fingerprint(connector_configuration),
            status="PUBLISHED",
            created_by_user_id=principal.user_id,
            reason="Initial connector configuration",
            created_at=now,
        ))
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
                "configuration_version": 1,
                "configuration_fingerprint": _configuration_fingerprint(connector_configuration),
                "application_route_type": payload.application_route_type,
                "configuration_keys": sorted(connector_configuration.keys()),
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

@router.get(
    "/sources/{source_id}/connectors/{connector_id}/configuration-versions",
    response_model=dict,
)
def list_source_connector_configuration_versions(
    request: Request,
    source_id: UUID,
    connector_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """List immutable connector configuration history with secrets redacted."""
    _require_admin(db, principal, request)
    connector = db.get(SourceConnector, connector_id)
    if connector is None or connector.source_id != source_id or db.get(Source, source_id) is None:
        raise HTTPException(status_code=404, detail="Source connector not found.")
    versions = db.scalars(
        select(SourceConnectorConfigurationVersion)
        .where(SourceConnectorConfigurationVersion.source_connector_id == connector.id)
        .order_by(SourceConnectorConfigurationVersion.version.desc())
    ).all()
    return {"data": [{
        "id": version.id,
        "version": version.version,
        "status": version.status,
        "configuration": _redact_connector_configuration(version.configuration),
        "configuration_fingerprint": version.configuration_fingerprint,
        "created_by_user_id": version.created_by_user_id,
        "created_at": version.created_at,
        "reason": version.reason,
        "active": version.version == connector.active_configuration_version,
    } for version in versions]}


@router.post(
    "/sources/{source_id}/connectors/{connector_id}/configuration-versions/{version_number}/activate",
    response_model=dict,
)
def activate_source_connector_configuration_version(
    source_id: UUID,
    connector_id: UUID,
    version_number: int,
    payload: SourceConnectorConfigurationActivationRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Activate an existing immutable configuration snapshot without editing it."""
    _require_admin(db, principal, request)
    source = db.scalar(select(Source).where(Source.id == source_id).with_for_update())
    connector = db.scalar(
        select(SourceConnector).where(
            SourceConnector.id == connector_id,
            SourceConnector.source_id == source_id,
        ).with_for_update()
    )
    if source is None or connector is None:
        raise HTTPException(status_code=404, detail="Source connector not found.")
    if payload.expected_revision != connector.revision:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SOURCE_CONNECTOR_REVISION_CONFLICT",
                "message": "The connector changed since it was loaded; refresh and retry.",
                "current_revision": connector.revision,
            },
        )
    target = db.scalar(select(SourceConnectorConfigurationVersion).where(
        SourceConnectorConfigurationVersion.source_connector_id == connector.id,
        SourceConnectorConfigurationVersion.version == version_number,
        SourceConnectorConfigurationVersion.status == "PUBLISHED",
    ))
    if target is None:
        raise HTTPException(status_code=404, detail="Published connector configuration version not found.")
    if target.version == connector.active_configuration_version:
        raise HTTPException(status_code=409, detail="The requested configuration version is already active.")
    validated_configuration = dict(target.configuration)
    _validate_connector_configuration(connector.connector_type, validated_configuration)
    _assert_configuration_target_allowed(db, connector.connector_type, validated_configuration)
    if validated_configuration != target.configuration:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SOURCE_CONFIGURATION_REQUIRES_REPUBLICATION",
                "message": "This published configuration predates URL normalization; create and publish a normalized version before activation.",
            },
        )

    now = datetime.now(timezone.utc)
    previous_version = connector.active_configuration_version
    connector.active_configuration_version = target.version
    connector.configuration = dict(target.configuration)
    connector.revision += 1
    connector.updated_at = now
    source.base_url = (
        target.configuration.get("endpoint_url")
        or target.configuration.get("feed_url")
        or ((target.configuration.get("start_urls") or [None])[0])
        or source.base_url
    )
    source.updated_at = now
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="SOURCE_CONNECTOR_CONFIGURATION_ROLLED_BACK",
        resource_type="source_connector",
        resource_id=connector.id,
        outcome="SUCCESS",
        details={
            "source_id": str(source.id),
            "connector_type": connector.connector_type,
            "previous_version": previous_version,
            "activated_version": target.version,
            "configuration_fingerprint": target.configuration_fingerprint,
            "reason": payload.reason,
        },
        request_id=request.headers.get("x-request-id"),
        created_at=now,
    ))
    db.commit()
    db.refresh(connector)
    return {"data": {
        "source_id": source.id,
        "connector_id": connector.id,
        "revision": connector.revision,
        "active_configuration_version": connector.active_configuration_version,
        "configuration_fingerprint": target.configuration_fingerprint,
        "activated_at": now,
        "configuration": _redact_connector_configuration(target.configuration),
    }}


@router.patch(
    "/sources/{source_id}/connectors/{connector_id}/configuration",
    response_model=dict,
)
def update_source_connector_configuration(
    source_id: UUID,
    connector_id: UUID,
    payload: SourceConnectorConfigurationUpdateRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Create an immutable draft; only the publish action changes active configuration."""
    _require_admin(db, principal, request)
    source = db.scalar(select(Source).where(Source.id == source_id).with_for_update())
    connector = db.scalar(
        select(SourceConnector).where(
            SourceConnector.id == connector_id,
            SourceConnector.source_id == source_id,
        ).with_for_update()
    )
    if source is None or connector is None:
        raise HTTPException(status_code=404, detail="Source connector not found.")
    if payload.expected_revision != connector.revision:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SOURCE_CONNECTOR_REVISION_CONFLICT",
                "message": "The connector changed since it was loaded; refresh and retry.",
                "current_revision": connector.revision,
            },
        )

    configuration = dict(payload.configuration)
    _validate_connector_configuration(connector.connector_type, configuration)
    _assert_configuration_target_allowed(db, connector.connector_type, configuration)
    fingerprint = _configuration_fingerprint(configuration)
    active = db.scalar(select(SourceConnectorConfigurationVersion).where(
        SourceConnectorConfigurationVersion.source_connector_id == connector.id,
        SourceConnectorConfigurationVersion.version == connector.active_configuration_version,
        SourceConnectorConfigurationVersion.status == "PUBLISHED",
    ))
    if active is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "SOURCE_CONFIGURATION_VERSION_MISSING", "message": "The active connector configuration version is unavailable."},
        )
    if active.configuration_fingerprint == fingerprint:
        raise HTTPException(
            status_code=409,
            detail={"code": "SOURCE_CONFIGURATION_UNCHANGED", "message": "The submitted configuration matches the active version."},
        )

    now = datetime.now(timezone.utc)
    next_version = int(db.scalar(
        select(func.max(SourceConnectorConfigurationVersion.version)).where(
            SourceConnectorConfigurationVersion.source_connector_id == connector.id,
        )
    ) or 0) + 1
    draft = SourceConnectorConfigurationVersion(
        source_connector_id=connector.id,
        version=next_version,
        configuration=configuration,
        configuration_fingerprint=fingerprint,
        status="DRAFT",
        created_by_user_id=principal.user_id,
        reason=payload.reason,
        created_at=now,
    )
    db.add(draft)
    connector.revision += 1
    connector.updated_at = now
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="SOURCE_CONNECTOR_CONFIGURATION_DRAFTED",
        resource_type="source_connector",
        resource_id=connector.id,
        outcome="SUCCESS",
        details={
            "source_id": str(source.id),
            "connector_type": connector.connector_type,
            "active_version": active.version,
            "draft_version": next_version,
            "configuration_fingerprint": fingerprint,
            "configuration_keys": sorted(configuration.keys()),
            "reason": payload.reason,
        },
        request_id=request.headers.get("x-request-id"),
        created_at=now,
    ))
    db.commit()
    db.refresh(connector)
    return {"data": {
        "source_id": source.id,
        "connector_id": connector.id,
        "revision": connector.revision,
        "status": "DRAFT",
        "draft_version": next_version,
        "active_configuration_version": connector.active_configuration_version,
        "configuration_fingerprint": fingerprint,
        "created_at": now,
        "configuration": _redact_connector_configuration(configuration),
    }}


@router.post(
    "/sources/{source_id}/connectors/{connector_id}/configuration-versions/{version_number}/publish",
    response_model=dict,
)
def publish_source_connector_configuration_version(
    source_id: UUID,
    connector_id: UUID,
    version_number: int,
    payload: SourceConnectorConfigurationActivationRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Validate and publish a draft, then atomically switch the active configuration."""
    _require_admin(db, principal, request)
    source = db.scalar(select(Source).where(Source.id == source_id).with_for_update())
    connector = db.scalar(
        select(SourceConnector).where(
            SourceConnector.id == connector_id,
            SourceConnector.source_id == source_id,
        ).with_for_update()
    )
    if source is None or connector is None:
        raise HTTPException(status_code=404, detail="Source connector not found.")
    if payload.expected_revision != connector.revision:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "SOURCE_CONNECTOR_REVISION_CONFLICT",
                "message": "The connector changed since it was loaded; refresh and retry.",
                "current_revision": connector.revision,
            },
        )
    draft = db.scalar(select(SourceConnectorConfigurationVersion).where(
        SourceConnectorConfigurationVersion.source_connector_id == connector.id,
        SourceConnectorConfigurationVersion.version == version_number,
        SourceConnectorConfigurationVersion.status == "DRAFT",
    ).with_for_update())
    if draft is None:
        raise HTTPException(status_code=404, detail="Connector configuration draft not found.")
    normalized_configuration = dict(draft.configuration)
    _validate_connector_configuration(connector.connector_type, normalized_configuration)
    _assert_configuration_target_allowed(db, connector.connector_type, normalized_configuration)
    draft.configuration = normalized_configuration
    draft.configuration_fingerprint = _configuration_fingerprint(normalized_configuration)
    active = db.scalar(select(SourceConnectorConfigurationVersion).where(
        SourceConnectorConfigurationVersion.source_connector_id == connector.id,
        SourceConnectorConfigurationVersion.version == connector.active_configuration_version,
        SourceConnectorConfigurationVersion.status == "PUBLISHED",
    ))
    if active is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "SOURCE_CONFIGURATION_VERSION_MISSING", "message": "The active connector configuration version is unavailable."},
        )

    now = datetime.now(timezone.utc)
    previous_version = active.version
    draft.status = "PUBLISHED"
    connector.configuration = dict(draft.configuration)
    connector.active_configuration_version = draft.version
    connector.revision += 1
    connector.updated_at = now
    source.base_url = (
        draft.configuration.get("endpoint_url")
        or draft.configuration.get("feed_url")
        or ((draft.configuration.get("start_urls") or [None])[0])
        or source.base_url
    )
    source.updated_at = now
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="SOURCE_CONNECTOR_CONFIGURATION_PUBLISHED",
        resource_type="source_connector",
        resource_id=connector.id,
        outcome="SUCCESS",
        details={
            "source_id": str(source.id),
            "connector_type": connector.connector_type,
            "previous_version": previous_version,
            "published_version": draft.version,
            "previous_fingerprint": active.configuration_fingerprint,
            "configuration_fingerprint": draft.configuration_fingerprint,
            "reason": payload.reason,
        },
        request_id=request.headers.get("x-request-id"),
        created_at=now,
    ))
    db.commit()
    db.refresh(connector)
    return {"data": {
        "source_id": source.id,
        "connector_id": connector.id,
        "revision": connector.revision,
        "status": "PUBLISHED",
        "active_configuration_version": connector.active_configuration_version,
        "configuration_fingerprint": draft.configuration_fingerprint,
        "published_at": now,
        "configuration": _redact_connector_configuration(draft.configuration),
    }}


@router.patch("/sources/{source_id}/permission-review", response_model=dict)
def update_source_permission_review(
    source_id: UUID,
    payload: SourcePermissionReviewRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Record the administrator's explicit terms/permission decision for automated acquisition."""
    _require_admin(db, principal, request)
    source = db.scalar(select(Source).where(Source.id == source_id).with_for_update())
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    if payload.status == "APPROVED":
        required_evidence = {
            "reference": payload.reference,
            "access_method": payload.access_method,
            "terms_reference": payload.terms_reference,
            "rate_limit_policy": payload.rate_limit_policy,
            "rate_limit_reference": payload.rate_limit_reference,
        }
        missing_fields = [
            field for field, value in required_evidence.items()
            if not isinstance(value, str) or not value.strip()
        ]
        if missing_fields:
            raise HTTPException(status_code=422, detail={
                "code": "PERMISSION_REVIEW_EVIDENCE_REQUIRED",
                "message": "An approved permission review requires a reference, access method, terms reference, rate-limit policy, and rate-limit reference.",
                "missing_fields": missing_fields,
            })

    now = datetime.now(timezone.utc)
    configuration = dict(source.configuration) if isinstance(source.configuration, dict) else {}
    previous_details = configuration.get("permission_review", {})
    previous = {
        "status": source.permission_review_status,
        "reference": source.permission_review_reference,
        "reviewed_at": source.permission_reviewed_at.isoformat() if source.permission_reviewed_at else None,
        "details": previous_details if isinstance(previous_details, dict) else {},
    }
    supplied_details = {
        "access_method": payload.access_method,
        "terms_reference": payload.terms_reference,
        "rate_limit_policy": payload.rate_limit_policy,
        "rate_limit_reference": payload.rate_limit_reference,
        "review_notes": payload.review_notes,
    }
    details_were_supplied = any(value is not None for value in supplied_details.values())
    if payload.status == "APPROVED" or details_were_supplied:
        configuration["permission_review"] = {
            key: value.strip() if isinstance(value, str) else value
            for key, value in supplied_details.items()
            if value is not None
        }
    source.configuration = configuration
    source.permission_review_status = payload.status
    source.permission_review_reference = payload.reference
    source.permission_reviewed_at = now if payload.status == "APPROVED" else None
    if payload.status == "APPROVED":
        if source.operational_state == "RESTRICTED" and source.last_error_category == "PERMISSION_REVIEW_REQUIRED":
            source.operational_state = "CONFIGURED"
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
            "permission_review_details": configuration.get("permission_review", {}),
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
    _require_admin(db, principal, request)
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
    """Probe a registered connector and persist the observed source outcome truthfully."""
    _require_admin(db, principal, request)
    source = db.scalar(select(Source).where(Source.id == source_id).with_for_update())
    connector_row = db.scalar(
        select(SourceConnector).where(
            SourceConnector.id == connector_id,
            SourceConnector.source_id == source_id,
        ).with_for_update()
    )
    if source is None or connector_row is None:
        raise HTTPException(status_code=404, detail="Source connector not found.")
    _enforce_source_permission_review(source, principal, request, db)

    started_at = datetime.now(timezone.utc)
    connector_type = connector_row.connector_type.strip().upper()
    context = dict(source.configuration or {}) if isinstance(source.configuration, dict) else {}
    context.update(_active_connector_configuration(db, connector_row))
    context.pop("permission_review", None)
    context["forbidden_target_rules"] = [
        {
            "id": str(rule.id),
            "rule_type": rule.rule_type,
            "pattern": rule.pattern,
            "connector_scope": rule.connector_scope,
            "reason": rule.reason,
            "active": rule.active,
        }
        for rule in db.scalars(
            select(ForbiddenTargetRule)
            .where(ForbiddenTargetRule.active.is_(True))
            .order_by(ForbiddenTargetRule.id)
        ).all()
    ]
    if connector_type == "JOBSPY":
        # Registered acquisition always supplies a live per-request policy guard.
        # JobSpy cannot honor that guard, so a direct probe without the guard would
        # report false health even though every registered acquisition is restricted.
        source.operational_state = "RESTRICTED"
        source.last_error_category = "JOBSPY_LIVE_POLICY_UNSUPPORTED"
        source.updated_at = started_at
        connector_row.status = "DEGRADED"
        connector_row.updated_at = started_at
        db.add(AdminAuditEvent(
            actor_user_id=principal.user_id,
            action="SOURCE_CONNECTOR_HEALTH_CHECK_BLOCKED",
            resource_type="source_connector",
            resource_id=connector_row.id,
            outcome="BLOCKED",
            details={
                "source_id": str(source.id),
                "connector_type": connector_type,
                "error_code": "JOBSPY_LIVE_POLICY_UNSUPPORTED",
                "source_operational_state": source.operational_state,
                "status": connector_row.status,
            },
            request_id=request.headers.get("x-request-id"),
            created_at=started_at,
        ))
        db.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "JOBSPY_LIVE_POLICY_UNSUPPORTED",
                "message": "The JOBSPY adapter cannot enforce live policy before every outbound request.",
            },
        )

    # CHECKED is an in-progress state. last_observed_at changes only when this probe ends.
    source.operational_state = "CHECKED"
    source.updated_at = started_at
    db.commit()

    probe_status = "SUCCEEDED"
    source_state = "HEALTHY"
    source_error_category: str | None = None
    try:
        if connector_type == GreenhouseJobBoardConnector.connector_type:
            health = GreenhouseJobBoardConnector().health_check(context=context)
            safe_details = {
                "probe_mode": "CONNECTOR_HEALTH_CHECK",
                "board_name": health.get("board_name"),
                "job_count": health.get("job_count"),
                "record_count": health.get("job_count"),
            }
        else:
            connector = AcquisitionConnectorRegistry().create(
                connector_row.connector_type, connector_row.connector_version,
            )
            acquisition_result = connector.acquire(source_id=source.id, context=context)
            raw_status = getattr(acquisition_result, "status", "SUCCEEDED")
            probe_status = getattr(raw_status, "value", raw_status)
            if not isinstance(probe_status, str):
                probe_status = "FAILED"
            raw_error = getattr(acquisition_result, "error_category", None)
            failures = getattr(acquisition_result, "failures", ())
            if not raw_error and failures:
                raw_error = getattr(failures[0], "category", None)
            source_state, source_error_category = classify_acquisition_outcome(
                status=probe_status,
                error_category=raw_error if isinstance(raw_error, str) else None,
            )
            safe_details = {
                "probe_mode": "BOUNDED_ACQUISITION_PROBE",
                "record_count": len(acquisition_result.records),
            }
    except Exception as exc:
        completed_at = datetime.now(timezone.utc)
        connector_error_category = getattr(exc, "error_category", None)
        if isinstance(exc, ValueError):
            connector_error_category = "INVALID_CONNECTOR_CONFIGURATION"
        if not isinstance(connector_error_category, str) or not connector_error_category.strip():
            connector_error_category = (
                "greenhouse_request_failed"
                if connector_type == GreenhouseJobBoardConnector.connector_type
                else "connector_health_check_failed"
            )
        raw_status = getattr(exc, "status", None)
        exception_status = getattr(raw_status, "value", raw_status)
        if not isinstance(exception_status, str) or exception_status.upper() not in {
            "RESTRICTED", "UNAVAILABLE", "FAILED", "PARTIAL", "PARTIALLY_SUCCEEDED"
        }:
            exception_status = "FAILED"
        source_state, source_error_category = classify_acquisition_outcome(
            status=exception_status,
            error_category=connector_error_category,
        )
        source.last_observed_at = completed_at
        source.operational_state = source_state
        source.last_error_category = source_error_category
        source.updated_at = completed_at
        connector_row.status = "DEGRADED"
        connector_row.last_verified_at = completed_at
        connector_row.updated_at = completed_at
        db.add(AdminAuditEvent(
            actor_user_id=principal.user_id,
            action="SOURCE_CONNECTOR_HEALTH_CHECK_FAILED",
            resource_type="source_connector",
            resource_id=connector_row.id,
            outcome="FAILED",
            details={
                "source_id": str(source.id),
                "connector_type": connector_row.connector_type,
                "error_code": source_error_category or connector_error_category,
                "source_operational_state": source_state,
                "status": connector_row.status,
            },
            request_id=request.headers.get("x-request-id"),
            created_at=completed_at,
        ))
        db.commit()
        raise HTTPException(
            status_code=502,
            detail={"code": "SOURCE_HEALTH_CHECK_FAILED", "message": "The source connector health check failed."},
        ) from exc

    completed_at = datetime.now(timezone.utc)
    source.last_observed_at = completed_at
    source.operational_state = source_state
    source.last_error_category = source_error_category
    source.updated_at = completed_at
    connector_row.status = {
        "HEALTHY": "ACTIVE",
        "DEGRADED": "DEGRADED",
        "RESTRICTED": "DEGRADED",
        "FAILED": "DEGRADED",
        "UNAVAILABLE": "DEGRADED",
        "CHECKED": "ACTIVE",
        "CONFIGURED": "ACTIVE",
    }.get(source_state, "DEGRADED")
    connector_row.last_verified_at = completed_at
    connector_row.updated_at = completed_at
    audit_outcome = {
        "HEALTHY": "SUCCESS",
        "DEGRADED": "PARTIAL",
        "RESTRICTED": "BLOCKED",
        "FAILED": "FAILED",
        "UNAVAILABLE": "UNAVAILABLE",
        "CHECKED": "PENDING",
        "CONFIGURED": "PENDING",
    }.get(source_state, "FAILED")
    safe_details.update({
        "probe_status": probe_status,
        "error_category": source_error_category,
        "source_operational_state": source_state,
    })
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="SOURCE_CONNECTOR_HEALTH_CHECKED",
        resource_type="source_connector",
        resource_id=connector_row.id,
        outcome=audit_outcome,
        details={
            "source_id": str(source.id),
            "connector_type": connector_row.connector_type,
            "configuration_version": connector_row.active_configuration_version,
            **safe_details,
            "status": connector_row.status,
        },
        request_id=request.headers.get("x-request-id"),
        created_at=completed_at,
    ))
    db.commit()
    db.refresh(connector_row)
    db.refresh(source)
    return {"data": {
        "source_id": str(source.id),
        "connector_id": str(connector_row.id),
        "configuration_version": connector_row.active_configuration_version,
        "status": connector_row.status,
        "last_verified_at": connector_row.last_verified_at,
        "source_last_observed_at": source.last_observed_at,
        "source_operational_state": source.operational_state,
        "source_last_error_category": source.last_error_category,
        **safe_details,
    }}

def _acquisition_processing_summary(db: Session, run_id: UUID) -> dict[str, int]:
    """Summarize canonicalization work for newly acquired and re-observed records."""
    acquired_rows = db.execute(
        select(OutboxEvent.status, func.count(OutboxEvent.id))
        .join(RawSourceRecord, OutboxEvent.aggregate_id == RawSourceRecord.id)
        .where(
            OutboxEvent.event_type == "RAW_SOURCE_RECORD_ACQUIRED",
            RawSourceRecord.acquisition_run_id == run_id,
        )
        .group_by(OutboxEvent.status)
    ).all()
    observed_rows = db.execute(
        select(OutboxEvent.status, func.count(OutboxEvent.id))
        .join(JobDiscoveryEvent, OutboxEvent.aggregate_id == JobDiscoveryEvent.id)
        .where(
            OutboxEvent.event_type == "RAW_SOURCE_RECORD_OBSERVED",
            JobDiscoveryEvent.acquisition_run_id == run_id,
        )
        .group_by(OutboxEvent.status)
    ).all()
    counts: dict[str, int] = {}
    for event_status, count in [*acquired_rows, *observed_rows]:
        key = str(event_status).upper()
        counts[key] = counts.get(key, 0) + int(count)
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
    """Run any configured, registered connector through the shared acquisition pipeline."""
    _require_admin(db, principal, request)
    source = db.scalar(select(Source).where(Source.id == source_id).with_for_update())
    connector_row = db.scalar(
        select(SourceConnector).where(
            SourceConnector.id == payload.connector_id,
            SourceConnector.source_id == source_id,
        ).with_for_update()
    )
    if source is None or connector_row is None:
        raise HTTPException(status_code=404, detail="Source connector not found.")
    if source.status != "ACTIVE" or connector_row.status != "ACTIVE":
        raise HTTPException(status_code=409, detail="Source and connector must both be active.")
    _enforce_source_permission_review(source, principal, request, db)

    context = dict(source.configuration or {}) if isinstance(source.configuration, dict) else {}
    context.update(_active_connector_configuration(db, connector_row))
    repository = SqlAlchemyAcquisitionRepository(db)
    context["forbidden_target_rules"] = repository.list_active_forbidden_target_rules()
    context["acquisition_policy_revalidator"] = build_acquisition_policy_revalidator(
        repository, source_id, connector_row.id, source, connector_row
    )
    try:
        if connector_row.connector_type == GreenhouseJobBoardConnector.connector_type:
            GreenhouseJobBoardConnector.validate_context(context)
            connector = GreenhouseJobBoardConnector()
        else:
            connector = AcquisitionConnectorRegistry().create(
                connector_row.connector_type, connector_row.connector_version,
            )
    except (ValueError, UnsupportedConnectorError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_CONNECTOR_CONFIGURATION", "message": "The configured source connector is invalid."},
        ) from exc

    existing_run = repository.get_run(source_id, payload.run_key)
    if existing_run is not None and existing_run.source_connector_id != connector_row.id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ACQUISITION_IDEMPOTENCY_KEY_CONFLICT",
                "message": "This run key is already bound to a different source connector.",
            },
        )
    service = AcquisitionService(repository, connector)
    try:
        result = service.run(
            source_id,
            connector_row.id,
            payload.run_key,
            context,
        )
    except Exception as exc:
        failed_run = SqlAlchemyAcquisitionRepository(db).get_run(source_id, payload.run_key)
        if failed_run is not None:
            if isinstance(exc, GreenhouseConnectorError):
                error_code = "greenhouse_request_failed"
            elif isinstance(exc, ValueError):
                error_code = "INVALID_CONNECTOR_CONFIGURATION"
            else:
                error_code = f"CONNECTOR_ERROR_{type(exc).__name__.upper()}"[:64]
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
                    "error_code": error_code,
                },
                request_id=request.headers.get("x-request-id"),
                created_at=datetime.now(timezone.utc),
            ))
            db.commit()
        if isinstance(exc, ValueError):
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_CONNECTOR_CONFIGURATION", "message": "The configured source connector is invalid."},
            ) from exc
        raise HTTPException(
            status_code=502,
            detail={"code": "SOURCE_ACQUISITION_FAILED", "message": "The configured source could not be acquired."},
        ) from exc

    terminal_outcomes = {
        "RESTRICTED": (
            502,
            "SOURCE_RESTRICTED",
            "The configured source restricted acquisition.",
        ),
        "UNAVAILABLE": (
            503,
            "SOURCE_UNAVAILABLE",
            "The configured source is unavailable.",
        ),
        "FAILED": (
            502,
            "SOURCE_ACQUISITION_FAILED",
            "The configured source could not be acquired.",
        ),
    }
    terminal_error = terminal_outcomes.get(result.status)
    if terminal_error is not None:
        status_code, error_code, message = terminal_error
        db.add(AdminAuditEvent(
            actor_user_id=principal.user_id,
            action="SOURCE_ACQUISITION_FAILED",
            resource_type="acquisition_run",
            resource_id=result.run_id,
            outcome=result.status,
            details={
                "source_id": str(source_id),
                "connector_id": str(connector_row.id),
                "run_key": payload.run_key,
                "error_code": result.error_category or error_code,
                "acquired_count": result.acquired_count,
                "persisted_count": result.persisted_count,
                "failed_item_count": result.failed_item_count,
                "retry_after_seconds": result.retry_after_seconds,
            },
            request_id=request.headers.get("x-request-id"),
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
        detail = {"code": error_code, "message": message}
        if result.retry_after_seconds is not None:
            detail["retry_after_seconds"] = result.retry_after_seconds
        raise HTTPException(status_code=status_code, detail=detail)

    persisted_run = db.get(AcquisitionRun, result.run_id)
    persisted_configuration = (
        db.get(SourceConnectorConfigurationVersion, persisted_run.configuration_version_id)
        if persisted_run is not None and persisted_run.configuration_version_id is not None
        else None
    )
    downstream_processing = _acquisition_processing_summary(db, result.run_id)
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action=(
            "SOURCE_ACQUISITION_REPLAYED"
            if existing_run is not None
            else "SOURCE_ACQUISITION_PARTIAL"
            if result.status == "PARTIAL"
            else "SOURCE_ACQUISITION_COMPLETED"
        ),
        resource_type="acquisition_run",
        resource_id=result.run_id,
        outcome=result.status,
        details={
            "source_id": str(source_id),
            "connector_id": str(connector_row.id),
            "run_key": payload.run_key,
            "configuration_version": persisted_configuration.version if persisted_configuration else None,
            "configuration_version_id": str(persisted_configuration.id) if persisted_configuration else None,
            "acquired_count": result.acquired_count,
            "persisted_count": result.persisted_count,
            "duplicate_count": result.duplicate_count,
            "observed_count": result.observed_count,
            "new_count": result.new_count,
            "changed_count": result.changed_count,
            "unchanged_count": result.unchanged_count,
            "error_category": result.error_category,
            "failed_item_count": result.failed_item_count,
            "retry_after_seconds": result.retry_after_seconds,
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
        "configuration_version": persisted_configuration.version if persisted_configuration else None,
        "configuration_version_id": str(persisted_configuration.id) if persisted_configuration else None,
        "status": result.status,
        "acquired_count": result.acquired_count,
        "persisted_count": result.persisted_count,
        "duplicate_count": result.duplicate_count,
        "observed_count": result.observed_count,
        "new_count": result.new_count,
        "changed_count": result.changed_count,
        "unchanged_count": result.unchanged_count,
        "error_category": result.error_category,
        "failed_item_count": result.failed_item_count,
        "retry_after_seconds": result.retry_after_seconds,
        "downstream_processing": downstream_processing,
    }}


@router.get(
    "/sources/{source_id}/acquisition-runs",
    response_model=dict,
)
def list_source_acquisition_runs(
    request: Request,
    source_id: UUID,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """List a source's persisted acquisition history with bounded pagination."""
    _require_admin(db, principal, request)
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
            "configuration_version_id": str(run.configuration_version_id) if run.configuration_version_id else None,
            "status": run.status,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "acquired_count": acquired_count,
            "persisted_count": persisted_count,
            "duplicate_count": max(acquired_count - persisted_count, 0),
            "observed_count": int(metrics.get("observed_count", 0)),
            "new_count": int(metrics.get("new_count", 0)),
            "changed_count": int(metrics.get("changed_count", 0)),
            "unchanged_count": int(metrics.get("unchanged_count", 0)),
            "error_category": metrics.get("error_category"),
            "failed_item_count": int(metrics.get("failed_item_count", 0)),
            "failure_categories": (
                [item for item in metrics.get("failure_categories", []) if isinstance(item, str)]
                if isinstance(metrics.get("failure_categories", []), list) else []
            ),
            "retry_after_seconds": metrics.get("retry_after_seconds"),
            "error_summary": run.error_summary,
            "downstream_processing": _acquisition_processing_summary(db, run.id),
        })
    return {"data": data, "pagination": {"limit": limit, "offset": offset, "total": total}}


@router.get(
    "/sources/{source_id}/acquisition-runs/{run_id}",
    response_model=dict,
)
def get_source_acquisition_run(
    request: Request,
    source_id: UUID,
    run_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return persisted acquisition state and a fresh downstream outbox summary."""
    _require_admin(db, principal, request)
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
        "configuration_version_id": str(run.configuration_version_id) if run.configuration_version_id else None,
        "status": run.status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "acquired_count": acquired_count,
        "persisted_count": persisted_count,
        "duplicate_count": max(acquired_count - persisted_count, 0),
        "observed_count": int(metrics.get("observed_count", 0)),
        "new_count": int(metrics.get("new_count", 0)),
        "changed_count": int(metrics.get("changed_count", 0)),
        "unchanged_count": int(metrics.get("unchanged_count", 0)),
        "error_category": metrics.get("error_category"),
        "failed_item_count": int(metrics.get("failed_item_count", 0)),
        "failure_categories": (
            [item for item in metrics.get("failure_categories", []) if isinstance(item, str)]
            if isinstance(metrics.get("failure_categories", []), list) else []
        ),
        "retry_after_seconds": metrics.get("retry_after_seconds"),
        "error_summary": run.error_summary,
        "downstream_processing": _acquisition_processing_summary(db, run.id),
    }}

RETRYABLE_CANONICALIZATION_EVENT_TYPES = frozenset({
    "RAW_SOURCE_RECORD_ACQUIRED",
    "RAW_SOURCE_RECORD_OBSERVED",
})


def _status_counts(db: Session, model: Any, column_name: str) -> dict[str, int]:
    column = getattr(model, column_name)
    rows = db.execute(
        select(column, func.count()).group_by(column)
    ).all()
    return {str(value or "UNKNOWN").upper(): int(count) for value, count in rows}


@router.get("/operations/summary", response_model=dict)
def get_operations_summary(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return bounded operational counts without exposing payloads or candidate data."""
    _require_admin(db, principal, request)
    return {"data": {
        "acquisition_runs": _status_counts(db, AcquisitionRun, "status"),
        "outbox_events": _status_counts(db, OutboxEvent, "status"),
        "work_items": _status_counts(db, WorkItem, "status"),
        "source_operational_states": _status_counts(db, Source, "operational_state"),
        "stale_running_acquisition_runs": int(db.scalar(
            select(func.count(AcquisitionRun.id)).where(
                AcquisitionRun.status == "RUNNING",
                AcquisitionRun.lease_expires_at.is_not(None),
                AcquisitionRun.lease_expires_at <= datetime.now(timezone.utc),
            )
        ) or 0),
        "expired_processing_outbox_events": int(db.scalar(
            select(func.count(OutboxEvent.id)).where(
                OutboxEvent.status == "PROCESSING",
                or_(
                    OutboxEvent.locked_at.is_(None),
                    OutboxEvent.locked_at <= datetime.now(timezone.utc) - timedelta(seconds=300),
                ),
            )
        ) or 0),
        "workers": {
            "stored_status_counts": _status_counts(db, WorkerHeartbeat, "status"),
            "stale_count": int(db.scalar(
                select(func.count(WorkerHeartbeat.id)).where(
                    WorkerHeartbeat.status.in_(["RUNNING", "IDLE"]),
                    WorkerHeartbeat.last_seen_at <= datetime.now(timezone.utc) - timedelta(seconds=120),
                )
            ) or 0),
            "latest_heartbeat_at": db.scalar(select(func.max(WorkerHeartbeat.last_seen_at))),
        },
    }}


@router.get("/operations/workers", response_model=dict)
def list_operational_workers(
    request: Request,
    worker_name: str | None = Query(default=None, min_length=2, max_length=64),
    status_filter: Literal["RUNNING", "IDLE", "STOPPED", "FAILED", "STALE"] | None = Query(default=None, alias="status"),
    stale_after_seconds: int = Query(default=120, ge=30, le=3600),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Report worker heartbeats and derive staleness without claiming process health from config."""
    _require_admin(db, principal, request)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=stale_after_seconds)
    filters = []
    if worker_name is not None:
        filters.append(WorkerHeartbeat.worker_name == worker_name.strip().lower())
    if status_filter == "STALE":
        filters.append(
            WorkerHeartbeat.status.in_(["RUNNING", "IDLE"])
            & (WorkerHeartbeat.last_seen_at <= cutoff)
        )
    elif status_filter in {"RUNNING", "IDLE"}:
        filters.append(
            (WorkerHeartbeat.status == status_filter)
            & (WorkerHeartbeat.last_seen_at > cutoff)
        )
    elif status_filter is not None:
        filters.append(WorkerHeartbeat.status == status_filter)

    total = int(db.scalar(
        select(func.count(WorkerHeartbeat.id)).where(*filters)
    ) or 0)
    rows = db.scalars(
        select(WorkerHeartbeat).where(*filters)
        .order_by(WorkerHeartbeat.last_seen_at.desc(), WorkerHeartbeat.id.desc())
        .limit(limit).offset(offset)
    ).all()
    items = []
    for row in rows:
        derived_status = row.status
        if row.status in {"RUNNING", "IDLE"} and row.last_seen_at <= cutoff:
            derived_status = "STALE"
        items.append({
            "id": str(row.id),
            "worker_name": row.worker_name,
            "instance_id": str(row.instance_id),
            "status": derived_status,
            "started_at": row.started_at,
            "last_seen_at": row.last_seen_at,
            "last_success_at": row.last_success_at,
            "last_error_category": row.last_error_category,
            "metrics": safe_worker_metrics(row.metrics or {}),
        })
    return {
        "data": items,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total": total,
            "stale_after_seconds": stale_after_seconds,
        },
    }


@router.get("/operations/work-items", response_model=dict)
def list_operational_work_items(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status", pattern="^(PENDING|QUEUED|RUNNING|PROCESSING|SUCCEEDED|COMPLETED|PARTIALLY_SUCCEEDED|RETRY_SCHEDULED|FAILED|CANCELLED)$"),
    work_type: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """List persisted work items with safe operational metadata only."""
    _require_admin(db, principal, request)
    statement = select(WorkItem)
    count_statement = select(func.count(WorkItem.id))
    if status_filter:
        statement = statement.where(WorkItem.status == status_filter)
        count_statement = count_statement.where(WorkItem.status == status_filter)
    if work_type:
        statement = statement.where(WorkItem.work_type == work_type)
        count_statement = count_statement.where(WorkItem.work_type == work_type)
    total = int(db.scalar(count_statement) or 0)
    items = db.scalars(
        statement.order_by(WorkItem.created_at.desc(), WorkItem.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {"data": [{
        "id": item.id,
        "work_type": item.work_type,
        "resource_type": item.resource_type,
        "resource_id": item.resource_id,
        "status": item.status,
        "attempt_count": item.attempt_count,
        "execution_id": item.execution_id,
        "error_code": item.error_code,
        "error_category": (
            (item.error_details or {}).get("category")
            if isinstance(item.error_details, dict) else None
        ),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "started_at": item.started_at,
        "completed_at": item.completed_at,
    } for item in items], "pagination": {"limit": limit, "offset": offset, "total": total}}


@router.get("/operations/work-items/{work_item_id}", response_model=dict)
def get_operational_work_item(
    request: Request,
    work_item_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Inspect a work item and its outbox lifecycle without returning payload contents."""
    _require_admin(db, principal, request)
    item = db.get(WorkItem, work_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Work item not found.")
    events = db.scalars(
        select(OutboxEvent)
        .where(
            OutboxEvent.aggregate_type == "WorkItem",
            OutboxEvent.aggregate_id == item.id,
        )
        .order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc())
    ).all()
    return {"data": {
        "id": item.id,
        "work_type": item.work_type,
        "resource_type": item.resource_type,
        "resource_id": item.resource_id,
        "status": item.status,
        "attempt_count": item.attempt_count,
        "execution_id": item.execution_id,
        "error_code": item.error_code,
        "error_category": (
            (item.error_details or {}).get("category")
            if isinstance(item.error_details, dict) else None
        ),
        "result_available": item.result is not None,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "started_at": item.started_at,
        "completed_at": item.completed_at,
        "outbox_events": [{
            "id": event.id,
            "event_type": event.event_type,
            "status": event.status,
            "attempt_count": event.attempt_count,
            "available_at": event.available_at,
            "locked_at": event.locked_at,
            "processed_at": event.processed_at,
            "last_error": event.last_error,
            "payload_keys": sorted((event.payload or {}).keys()) if isinstance(event.payload, dict) else [],
        } for event in events],
    }}



MAX_ADMIN_WORK_ITEM_REPLAYS = 3
RETRYABLE_ADMIN_WORK_TYPE = "DISCOVERY_MISSION"


def _record_work_item_action(
    db: Session,
    *,
    actor_id: UUID,
    action: str,
    item: WorkItem,
    request: Request,
    now: datetime,
    details: dict[str, Any],
) -> None:
    db.add(AdminAuditEvent(
        actor_user_id=actor_id,
        action=action,
        resource_type="work_item",
        resource_id=item.id,
        outcome="SUCCESS",
        details=details,
        request_id=request.headers.get("x-request-id"),
        created_at=now,
    ))


def _work_item_action_response(item: WorkItem, mission: DiscoveryMission, action: str) -> dict[str, Any]:
    return {"data": {
        "id": item.id,
        "work_type": item.work_type,
        "status": item.status,
        "attempt_count": item.attempt_count,
        "execution_id": item.execution_id,
        "mission_status": mission.status,
        "next_attempt_at": mission.next_attempt_at,
        "updated_at": item.updated_at,
        "action": action,
    }}


@router.post("/operations/work-items/{work_item_id}/retry", response_model=dict)
def retry_operational_work_item(
    work_item_id: UUID,
    payload: WorkItemActionRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Expedite an already scheduled retry without creating another queue or work item."""
    _require_admin(db, principal, request)
    item = db.scalar(select(WorkItem).where(WorkItem.id == work_item_id).with_for_update())
    if item is None:
        raise HTTPException(status_code=404, detail="Work item not found.")
    if item.work_type != RETRYABLE_ADMIN_WORK_TYPE or item.resource_id is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "WORK_ITEM_RETRY_UNSUPPORTED", "message": "No safe administrative retry contract exists for this work type."},
        )
    mission = db.scalar(
        select(DiscoveryMission).where(DiscoveryMission.id == item.resource_id).with_for_update()
    )
    if mission is None:
        raise HTTPException(status_code=409, detail="The work item's discovery mission is missing.")
    now = datetime.now(timezone.utc)
    retry_at = mission.next_attempt_at
    if retry_at is not None and retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    if (
        item.status != "PENDING"
        or mission.status != "RETRY_WAIT"
        or retry_at is None
        or retry_at <= now
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "WORK_ITEM_NOT_RETRYABLE", "message": "Only a future-scheduled retry can be expedited."},
        )

    previous_next_attempt_at = mission.next_attempt_at
    mission.next_attempt_at = now
    mission.updated_at = now
    mission.revision = int(mission.revision or 1) + 1
    item.updated_at = now
    item.revision = int(item.revision or 1) + 1
    _record_work_item_action(
        db,
        actor_id=principal.user_id,
        action="WORK_ITEM_RETRY_REQUESTED",
        item=item,
        request=request,
        now=now,
        details={
            "work_type": item.work_type,
            "previous_status": item.status,
            "mission_status": mission.status,
            "previous_next_attempt_at": previous_next_attempt_at.isoformat(),
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(item)
    db.refresh(mission)
    return _work_item_action_response(item, mission, "RETRY_REQUESTED")


@router.post("/operations/work-items/{work_item_id}/replay", response_model=dict)
def replay_operational_work_item(
    work_item_id: UUID,
    payload: WorkItemActionRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Replay one terminally failed discovery mission using its existing durable work item."""
    _require_admin(db, principal, request)
    item = db.scalar(select(WorkItem).where(WorkItem.id == work_item_id).with_for_update())
    if item is None:
        raise HTTPException(status_code=404, detail="Work item not found.")
    if item.work_type != RETRYABLE_ADMIN_WORK_TYPE or item.resource_id is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "WORK_ITEM_REPLAY_UNSUPPORTED", "message": "No safe replay contract exists for this work type."},
        )
    mission = db.scalar(
        select(DiscoveryMission).where(DiscoveryMission.id == item.resource_id).with_for_update()
    )
    if mission is None:
        raise HTTPException(status_code=409, detail="The work item's discovery mission is missing.")
    if item.status != "FAILED" or mission.status != "FAILED":
        raise HTTPException(
            status_code=409,
            detail={"code": "WORK_ITEM_NOT_REPLAYABLE", "message": "Only a terminally failed discovery mission can be replayed."},
        )
    policy = mission.policy_snapshot if isinstance(mission.policy_snapshot, dict) else {}
    waves = policy.get("waves")
    if not isinstance(waves, list) or not waves or mission.error_code == "INVALID_SOURCE_FILTERS":
        raise HTTPException(
            status_code=409,
            detail={"code": "WORK_ITEM_REPLAY_UNSAFE", "message": "The persisted mission policy does not support a safe replay."},
        )
    replay_count = int(db.scalar(select(func.count(AdminAuditEvent.id)).where(
        AdminAuditEvent.action == "WORK_ITEM_REPLAY_REQUESTED",
        AdminAuditEvent.resource_type == "work_item",
        AdminAuditEvent.resource_id == item.id,
        AdminAuditEvent.outcome == "SUCCESS",
    )) or 0)
    if replay_count >= MAX_ADMIN_WORK_ITEM_REPLAYS:
        raise HTTPException(
            status_code=409,
            detail={"code": "WORK_ITEM_REPLAY_LIMIT_REACHED", "message": "The administrative replay limit has been reached for this work item."},
        )

    now = datetime.now(timezone.utc)
    previous_status = item.status
    previous_mission_status = mission.status
    mission.status = "PENDING"
    mission.wave_index = 0
    mission.current_wave_key = None
    mission.cumulative_usable_opportunities = 0
    mission.started_at = None
    mission.completed_at = None
    mission.next_attempt_at = now
    mission.error_code = None
    mission.updated_at = now
    mission.revision = int(mission.revision or 1) + 1

    item.status = "PENDING"
    item.started_at = None
    item.completed_at = None
    item.error_code = None
    item.error_details = {}
    item.result = None
    item.execution_id = uuid4()
    item.updated_at = now
    item.revision = int(item.revision or 1) + 1
    # Preserve attempt_count and all prior source-run rows as execution history.
    _record_work_item_action(
        db,
        actor_id=principal.user_id,
        action="WORK_ITEM_REPLAY_REQUESTED",
        item=item,
        request=request,
        now=now,
        details={
            "work_type": item.work_type,
            "previous_status": previous_status,
            "previous_mission_status": previous_mission_status,
            "replay_number": replay_count + 1,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(item)
    db.refresh(mission)
    return _work_item_action_response(item, mission, "REPLAY_REQUESTED")


@router.post("/operations/work-items/{work_item_id}/cancel", response_model=dict)
def cancel_operational_work_item(
    work_item_id: UUID,
    payload: WorkItemActionRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Cancel only unclaimed discovery work; a running worker wins the row-lock race."""
    _require_admin(db, principal, request)
    item = db.scalar(select(WorkItem).where(WorkItem.id == work_item_id).with_for_update())
    if item is None:
        raise HTTPException(status_code=404, detail="Work item not found.")
    if item.work_type != RETRYABLE_ADMIN_WORK_TYPE or item.resource_id is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "WORK_ITEM_CANCEL_UNSUPPORTED", "message": "This work type does not expose a safe cancellation contract."},
        )
    mission = db.scalar(
        select(DiscoveryMission).where(DiscoveryMission.id == item.resource_id).with_for_update()
    )
    if mission is None:
        raise HTTPException(status_code=409, detail="The work item's discovery mission is missing.")
    if (
        item.status != "PENDING"
        or mission.status not in {"PENDING", "RETRY_WAIT", "WAITING_FOR_CANONICALIZATION"}
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "WORK_ITEM_NOT_CANCELLABLE", "message": "Only unclaimed, nonterminal discovery work can be cancelled."},
        )

    now = datetime.now(timezone.utc)
    previous_status = item.status
    previous_mission_status = mission.status
    mission.status = "CANCELLED"
    mission.error_code = "CANCELLED_BY_ADMIN"
    mission.completed_at = now
    mission.next_attempt_at = None
    mission.updated_at = now
    mission.revision = int(mission.revision or 1) + 1

    item.status = "CANCELLED"
    item.started_at = None
    item.completed_at = now
    item.error_code = "CANCELLED_BY_ADMIN"
    item.error_details = {"category": "ADMIN_CANCELLED"}
    item.result = {"mission_status": "CANCELLED", "reason": "administrator_requested"}
    item.updated_at = now
    item.revision = int(item.revision or 1) + 1
    enqueue_discovery_completion_notification(db, mission, now)
    _record_work_item_action(
        db,
        actor_id=principal.user_id,
        action="WORK_ITEM_CANCELLED",
        item=item,
        request=request,
        now=now,
        details={
            "work_type": item.work_type,
            "previous_status": previous_status,
            "previous_mission_status": previous_mission_status,
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(item)
    db.refresh(mission)
    return _work_item_action_response(item, mission, "CANCELLED")


@router.get("/operations/outbox-events", response_model=dict)
def list_operational_outbox_events(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status", pattern="^(PENDING|PROCESSING|PROCESSED|FAILED|DEAD)$"),
    event_type: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """List outbox delivery state without disclosing stored event payloads."""
    _require_admin(db, principal, request)
    statement = select(OutboxEvent)
    count_statement = select(func.count(OutboxEvent.id))
    if status_filter:
        statement = statement.where(OutboxEvent.status == status_filter)
        count_statement = count_statement.where(OutboxEvent.status == status_filter)
    if event_type:
        statement = statement.where(OutboxEvent.event_type == event_type)
        count_statement = count_statement.where(OutboxEvent.event_type == event_type)
    total = int(db.scalar(count_statement) or 0)
    events = db.scalars(
        statement.order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return {"data": [{
        "id": event.id,
        "event_type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "status": event.status,
        "attempt_count": event.attempt_count,
        "available_at": event.available_at,
        "locked_at": event.locked_at,
        "processed_at": event.processed_at,
        "last_error": event.last_error,
        "payload_keys": sorted((event.payload or {}).keys()) if isinstance(event.payload, dict) else [],
        "created_at": event.created_at,
    } for event in events], "pagination": {"limit": limit, "offset": offset, "total": total}}


@router.post("/operations/acquisition-runs/recover-expired", response_model=dict)
def recover_expired_acquisition_runs(
    payload: OperationalRecoveryRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Explicitly recover only acquisition runs whose persisted lease has expired."""
    _require_admin(db, principal, request)
    now = datetime.now(timezone.utc)
    recovered = SqlAlchemyAcquisitionRepository(db).recover_stale_runs(
        now=now,
        limit=payload.limit,
    )
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="EXPIRED_ACQUISITION_RUNS_RECOVERED",
        resource_type="acquisition_run",
        resource_id=None,
        outcome="SUCCESS",
        details={"recovered_count": recovered, "limit": payload.limit, "reason": payload.reason},
        request_id=request.headers.get("x-request-id"),
        created_at=now,
    ))
    db.commit()
    return {"data": {"recovered_count": recovered, "limit": payload.limit, "recovered_at": now}}


@router.post("/operations/outbox-events/{event_id}/retry", response_model=dict)
def retry_dead_outbox_event(
    event_id: UUID,
    payload: OutboxRetryRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Requeue a dead canonicalization event only when a production handler exists."""
    _require_admin(db, principal, request)
    event = db.scalar(select(OutboxEvent).where(OutboxEvent.id == event_id).with_for_update())
    if event is None:
        raise HTTPException(status_code=404, detail="Outbox event not found.")
    if event.status not in {"FAILED", "DEAD"}:
        raise HTTPException(
            status_code=409,
            detail={"code": "OUTBOX_EVENT_NOT_TERMINAL", "message": "Only failed or dead events can be retried."},
        )
    if event.event_type not in RETRYABLE_CANONICALIZATION_EVENT_TYPES:
        raise HTTPException(
            status_code=409,
            detail={"code": "OUTBOX_EVENT_TYPE_NOT_RETRYABLE", "message": "No approved production retry handler is registered for this event type."},
        )
    raw_id_value = (event.payload or {}).get("raw_source_record_id") if isinstance(event.payload, dict) else None
    try:
        raw_id = UUID(str(raw_id_value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "OUTBOX_EVENT_PAYLOAD_INVALID", "message": "The event does not contain a valid raw source record reference."},
        ) from exc
    if db.get(RawSourceRecord, raw_id) is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "OUTBOX_EVENT_SOURCE_RECORD_MISSING", "message": "The source record required for retry no longer exists."},
        )
    if event.event_type == "RAW_SOURCE_RECORD_ACQUIRED":
        aggregate_matches = (
            event.aggregate_type == "RawSourceRecord"
            and event.aggregate_id == raw_id
        )
    else:
        discovery_event = (
            db.get(JobDiscoveryEvent, event.aggregate_id)
            if event.aggregate_type == "JobDiscoveryEvent" and event.aggregate_id is not None
            else None
        )
        aggregate_matches = (
            discovery_event is not None
            and discovery_event.raw_source_record_id == raw_id
        )
    if not aggregate_matches:
        raise HTTPException(
            status_code=409,
            detail={"code": "OUTBOX_EVENT_AGGREGATE_MISMATCH", "message": "The event aggregate does not match its source-record reference."},
        )

    now = datetime.now(timezone.utc)
    previous_status = event.status
    previous_attempt_count = event.attempt_count
    event.status = "PENDING"
    event.attempt_count = 0
    event.available_at = now
    event.locked_at = None
    event.processed_at = None
    event.last_error = None
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="OUTBOX_EVENT_RETRY_REQUESTED",
        resource_type="outbox_event",
        resource_id=event.id,
        outcome="SUCCESS",
        details={
            "event_type": event.event_type,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": str(event.aggregate_id) if event.aggregate_id else None,
            "previous_status": previous_status,
            "previous_attempt_count": previous_attempt_count,
            "reason": payload.reason,
        },
        request_id=request.headers.get("x-request-id"),
        created_at=now,
    ))
    db.commit()
    db.refresh(event)
    return {"data": {
        "id": event.id,
        "event_type": event.event_type,
        "status": event.status,
        "attempt_count": event.attempt_count,
        "available_at": event.available_at,
        "retry_requested_at": now,
    }}

@router.get("/users", response_model=dict)
def list_admin_users(
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    query: str | None = Query(default=None, min_length=1, max_length=120),
    account_status: Literal["ACTIVE", "SUSPENDED", "DEACTIVATED"] | None = None,
    email_verified: bool | None = None,
    support_status: Literal["OPEN", "RESOLVED", "CLOSED"] | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    last_login_after: datetime | None = None,
    last_login_before: datetime | None = None,
    last_activity_after: datetime | None = None,
    last_activity_before: datetime | None = None,
    sort_by: Literal["created_at", "display_name", "last_login_at", "last_activity_at", "status"] = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return a bounded user directory with approved filters and safe account summaries."""
    _require_admin(db, principal, request)

    from app.infrastructure.db.models.candidate import CandidateProfile
    from app.infrastructure.db.models.support import SupportTicket

    date_bounds = [
        ("created_after", created_after),
        ("created_before", created_before),
        ("last_login_after", last_login_after),
        ("last_login_before", last_login_before),
        ("last_activity_after", last_activity_after),
        ("last_activity_before", last_activity_before),
    ]
    normalized_bounds = {}
    for name, value in date_bounds:
        normalized_bounds[name] = (
            value.replace(tzinfo=timezone.utc)
            if value is not None and value.tzinfo is None
            else value
        )
    created_after = normalized_bounds["created_after"]
    created_before = normalized_bounds["created_before"]
    last_login_after = normalized_bounds["last_login_after"]
    last_login_before = normalized_bounds["last_login_before"]
    last_activity_after = normalized_bounds["last_activity_after"]
    last_activity_before = normalized_bounds["last_activity_before"]

    if created_after is not None and created_before is not None and created_after > created_before:
        raise HTTPException(status_code=422, detail="created_after must be <= created_before.")
    if last_login_after is not None and last_login_before is not None and last_login_after > last_login_before:
        raise HTTPException(status_code=422, detail="last_login_after must be <= last_login_before.")
    if last_activity_after is not None and last_activity_before is not None and last_activity_after > last_activity_before:
        raise HTTPException(status_code=422, detail="last_activity_after must be <= last_activity_before.")
    if query is not None and not query.strip():
        raise HTTPException(status_code=422, detail="Search query must not be blank.")

    last_activity_expression = select(func.max(AuditEvent.occurred_at)).where(
        AuditEvent.actor_id == User.id
    ).correlate(User).scalar_subquery()
    statement = select(
        User, CandidateProfile, last_activity_expression.label("last_activity_at")
    ).outerjoin(CandidateProfile, CandidateProfile.user_id == User.id)
    count_statement = select(func.count(User.id)).select_from(User).outerjoin(
        CandidateProfile, CandidateProfile.user_id == User.id
    )
    filters = []
    if account_status is not None:
        filters.append(User.status == account_status)
    if email_verified is not None:
        filters.append(User.email_verified.is_(email_verified))
    if created_after is not None:
        filters.append(User.created_at >= created_after)
    if created_before is not None:
        filters.append(User.created_at <= created_before)
    if last_login_after is not None:
        filters.append(User.last_login_at >= last_login_after)
    if last_login_before is not None:
        filters.append(User.last_login_at <= last_login_before)
    if last_activity_after is not None:
        filters.append(last_activity_expression >= last_activity_after)
    if last_activity_before is not None:
        filters.append(last_activity_expression <= last_activity_before)
    if support_status is not None:
        filters.append(User.id.in_(
            select(SupportTicket.candidate_user_id).where(SupportTicket.status == support_status)
        ))
    if query is not None:
        normalized_query = query.strip()
        pattern = f"%{normalized_query}%"
        search_terms = [User.display_name.ilike(pattern), User.email.ilike(pattern)]
        try:
            search_user_id = UUID(normalized_query)
        except ValueError:
            search_user_id = None
        if search_user_id is not None:
            search_terms.append(User.id == search_user_id)
        filters.append(or_(*search_terms))

    if filters:
        statement = statement.where(*filters)
        count_statement = count_statement.where(*filters)

    total = db.scalar(count_statement) or 0
    sort_columns = {
        "created_at": User.created_at,
        "display_name": func.lower(User.display_name),
        "last_login_at": User.last_login_at,
        "last_activity_at": last_activity_expression,
        "status": User.status,
    }
    sort_column = sort_columns[sort_by]
    primary_order = sort_column.asc() if sort_order == "asc" else sort_column.desc()
    tie_breaker = User.id.asc() if sort_order == "asc" else User.id.desc()
    rows = db.execute(
        statement.order_by(primary_order, tie_breaker).limit(limit).offset(offset)
    ).all()

    items = []
    for user, profile, last_activity_at in rows:
        items.append({
            "id": user.id,
            "display_name": user.display_name,
            "email": user.email,
            "email_verified": user.email_verified,
            "status": user.status,
            "onboarding_status": user.onboarding_status,
            "last_login_at": user.last_login_at,
            "last_activity_at": last_activity_at,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "candidate_profile": None if profile is None else {
                "id": profile.id,
                "status": profile.status,
                "revision": profile.revision,
                "updated_at": profile.updated_at,
            },
        })
    return {
        "data": {
            "items": items,
            "pagination": {"limit": limit, "offset": offset, "total": total},
        }
    }


@router.get("/users/{user_id}", response_model=dict)
def get_admin_user_detail(
    user_id: UUID,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return a minimized user record, scoped resource summaries, and an audit record."""
    _require_admin(db, principal, request)

    from app.infrastructure.db.models.candidate import CandidateProfile
    from app.infrastructure.db.models.documents import Document
    from app.infrastructure.db.models.discovery import CandidateDiscoverySettings, DiscoveryMission
    from app.infrastructure.db.models.notifications import (
        Notification,
        NotificationEmailPreference,
    )
    from app.infrastructure.db.models.opportunities import Application, CandidateEvaluation, Punt
    from app.infrastructure.db.models.preferences import CandidatePreferences
    from app.infrastructure.db.models.support import SupportTicket

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Target user not found.")
    profile = db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user.id))

    preferences = None
    discovery_overview = {
        "mission_count": 0,
        "mission_status_counts": {},
        "settings": None,
    }
    document_overview = {"total": 0, "by_type": {}, "latest_created_at": None}
    job_search_overview = {"evaluation_count": 0, "punt_count": 0}
    application_overview = {"total": 0, "by_status": {}}

    if profile is not None:
        preference_record = db.scalar(select(CandidatePreferences).where(
            CandidatePreferences.candidate_profile_id == profile.id
        ))
        preferences = preference_record.preferences if preference_record is not None else None

        document_overview["total"] = int(db.scalar(
            select(func.count(Document.id)).where(Document.candidate_profile_id == profile.id)
        ) or 0)
        document_overview["latest_created_at"] = db.scalar(
            select(func.max(Document.created_at)).where(Document.candidate_profile_id == profile.id)
        )
        document_types = db.execute(
            select(Document.document_type, func.count(Document.id))
            .where(Document.candidate_profile_id == profile.id)
            .group_by(Document.document_type)
            .order_by(Document.document_type)
        ).all()
        document_overview["by_type"] = {kind: count for kind, count in document_types}

        job_search_overview["evaluation_count"] = int(db.scalar(
            select(func.count(CandidateEvaluation.id)).where(
                CandidateEvaluation.candidate_profile_id == profile.id
            )
        ) or 0)
        job_search_overview["punt_count"] = int(db.scalar(
            select(func.count(Punt.id)).where(Punt.candidate_profile_id == profile.id)
        ) or 0)

        mission_count = int(db.scalar(
            select(func.count(DiscoveryMission.id)).where(
                DiscoveryMission.candidate_profile_id == profile.id
            )
        ) or 0)
        mission_status_rows = db.execute(
            select(DiscoveryMission.status, func.count(DiscoveryMission.id))
            .where(DiscoveryMission.candidate_profile_id == profile.id)
            .group_by(DiscoveryMission.status)
            .order_by(DiscoveryMission.status)
        ).all()
        settings = db.scalar(select(CandidateDiscoverySettings).where(
            CandidateDiscoverySettings.candidate_profile_id == profile.id
        ))
        discovery_overview = {
            "mission_count": mission_count,
            "mission_status_counts": {state: count for state, count in mission_status_rows},
            "settings": None if settings is None else {
                "status": settings.status,
                "schedule_type": settings.schedule_type,
                "next_run_at": settings.next_run_at,
                "last_scheduled_at": settings.last_scheduled_at,
            },
        }

        application_rows = db.execute(
            select(Application.status, func.count(Application.id))
            .where(Application.candidate_profile_id == profile.id)
            .group_by(Application.status)
            .order_by(Application.status)
        ).all()
        application_overview = {
            "total": sum(count for _, count in application_rows),
            "by_status": {state: count for state, count in application_rows},
        }

    from app.infrastructure.db.models.governance import AuditEvent

    recent_activity = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.actor_id == user.id)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(10)
    ).all()
    activity_overview = {
        "recent_event_count": len(recent_activity),
        "last_recorded_at": recent_activity[0].occurred_at if recent_activity else None,
        "recent_events": [{
            "event_type": event.event_type,
            "actor_type": event.actor_type,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "occurred_at": event.occurred_at,
            "request_id": event.request_id,
        } for event in recent_activity],
    }

    support_rows = db.execute(
        select(SupportTicket.status, func.count(SupportTicket.id))
        .where(SupportTicket.candidate_user_id == user.id)
        .group_by(SupportTicket.status)
        .order_by(SupportTicket.status)
    ).all()
    recent_tickets = db.scalars(
        select(SupportTicket)
        .where(SupportTicket.candidate_user_id == user.id)
        .order_by(SupportTicket.updated_at.desc(), SupportTicket.id.desc())
        .limit(5)
    ).all()
    notification_count = int(db.scalar(
        select(func.count(Notification.id)).where(Notification.user_id == user.id)
    ) or 0)
    unread_notification_count = int(db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id,
            Notification.read_at.is_(None),
        )
    ) or 0)
    email_preference = db.get(NotificationEmailPreference, user.id)

    now = datetime.now(timezone.utc)
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="ADMIN_USER_DETAIL_VIEWED",
        resource_type="user",
        resource_id=user.id,
        outcome="SUCCESS",
        details={
            "candidate_profile_present": profile is not None,
            "includes_preferences": preferences is not None,
            "document_count": document_overview["total"],
            "application_count": application_overview["total"],
        },
        request_id=request.headers.get("x-request-id"),
        created_at=now,
    ))
    db.commit()

    return {
        "data": {
            "id": user.id,
            "display_name": user.display_name,
            "email": user.email,
            "email_verified": user.email_verified,
            "photo_url": user.photo_url,
            "status": user.status,
            "onboarding_status": user.onboarding_status,
            "last_login_at": user.last_login_at,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "revision": user.revision,
            "candidate_profile": None if profile is None else {
                "id": profile.id,
                "status": profile.status,
                "revision": profile.revision,
                "profile_data": profile.profile_data,
                "created_at": profile.created_at,
                "updated_at": profile.updated_at,
            },
            "preferences": preferences,
            "activity_overview": activity_overview,
            "discovery_overview": discovery_overview,
            "job_search_overview": job_search_overview,
            "document_overview": document_overview,
            "application_overview": application_overview,
            "support_overview": {
                "total": sum(count for _, count in support_rows),
                "by_status": {state: count for state, count in support_rows},
                "recent_tickets": [{
                    "id": ticket.id,
                    "status": ticket.status,
                    "created_at": ticket.created_at,
                    "updated_at": ticket.updated_at,
                } for ticket in recent_tickets],
            },
            "notification_overview": {
                "total": notification_count,
                "unread": unread_notification_count,
                "email_enabled": bool(email_preference and email_preference.enabled),
            },
        }
    }


@router.get("/notifications/email-deliveries", response_model=dict)
def list_admin_email_deliveries(
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    delivery_status: Literal[
        "PENDING", "SENDING", "SENT", "FAILED", "DISABLED",
        "SKIPPED_UNVERIFIED", "SKIPPED_UNCONFIGURED",
    ] | None = Query(default=None, alias="status"),
    user_id: UUID | None = None,
    updated_after: datetime | None = None,
    updated_before: datetime | None = None,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Expose persisted email delivery outcomes without revealing message bodies or addresses."""
    _require_admin(db, principal, request)

    from app.infrastructure.db.models.notifications import Notification, NotificationEmailDelivery

    if updated_after is not None and updated_after.tzinfo is None:
        updated_after = updated_after.replace(tzinfo=timezone.utc)
    if updated_before is not None and updated_before.tzinfo is None:
        updated_before = updated_before.replace(tzinfo=timezone.utc)
    if updated_after is not None and updated_before is not None and updated_after > updated_before:
        raise HTTPException(status_code=422, detail="updated_after must be <= updated_before.")

    statement = select(NotificationEmailDelivery, Notification).join(
        Notification, Notification.id == NotificationEmailDelivery.notification_id
    )
    count_statement = select(func.count(NotificationEmailDelivery.id)).select_from(
        NotificationEmailDelivery
    ).join(Notification, Notification.id == NotificationEmailDelivery.notification_id)
    filters = []
    if delivery_status is not None:
        filters.append(NotificationEmailDelivery.status == delivery_status)
    if user_id is not None:
        filters.append(Notification.user_id == user_id)
    if updated_after is not None:
        filters.append(NotificationEmailDelivery.updated_at >= updated_after)
    if updated_before is not None:
        filters.append(NotificationEmailDelivery.updated_at <= updated_before)
    if filters:
        statement = statement.where(*filters)
        count_statement = count_statement.where(*filters)

    total = db.scalar(count_statement) or 0
    rows = db.execute(
        statement.order_by(
            NotificationEmailDelivery.updated_at.desc(),
            NotificationEmailDelivery.id.desc(),
        ).limit(limit).offset(offset)
    ).all()
    items = [{
        "id": delivery.id,
        "notification_id": notification.id,
        "user_id": notification.user_id,
        "event_type": notification.event_type,
        "resource_type": notification.resource_type,
        "resource_id": notification.resource_id,
        "status": delivery.status,
        "attempt_count": delivery.attempt_count,
        "last_error": delivery.last_error,
        "sent_at": delivery.sent_at,
        "updated_at": delivery.updated_at,
    } for delivery, notification in rows]
    return {
        "data": items,
        "pagination": {"limit": limit, "offset": offset, "total": total},
    }

def _latest_integration_observation(db: Session, integration_key: str) -> IntegrationHealthObservation | None:
    return db.scalar(
        select(IntegrationHealthObservation)
        .where(IntegrationHealthObservation.integration_key == integration_key)
        .order_by(
            IntegrationHealthObservation.observed_at.desc(),
            IntegrationHealthObservation.id.desc(),
        )
        .limit(1)
    )


@router.post("/notifications/email-deliveries/{delivery_id}/retry", response_model=dict)
def retry_admin_email_delivery(
    delivery_id: UUID,
    payload: EmailDeliveryRetryRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Queue a safe retry for a failed or previously unconfigured notification email."""
    _require_admin(db, principal, request)
    from app.config.settings import get_settings
    from app.infrastructure.db.models.notifications import (
        Notification, NotificationEmailDelivery, NotificationEmailPreference,
    )

    def reject_retry(
        *, code: str, message: str, outcome: str, validation_result: str,
        previous_status: str | None = None, http_status: int = 409,
    ) -> None:
        now = datetime.now(timezone.utc)
        db.add(AdminAuditEvent(
            actor_user_id=principal.user_id,
            action="NOTIFICATION_EMAIL_RETRY_REJECTED",
            resource_type="notification_email_delivery",
            resource_id=delivery_id,
            outcome=outcome,
            details={
                "validation_result": validation_result,
                "reason": payload.reason,
                "previous_delivery_status": previous_status,
            },
            request_id=request.headers.get("x-request-id"),
            created_at=now,
        ))
        db.commit()
        raise HTTPException(status_code=http_status, detail={"code": code, "message": message})

    delivery = db.scalar(
        select(NotificationEmailDelivery)
        .where(NotificationEmailDelivery.id == delivery_id)
        .with_for_update()
    )
    if delivery is None:
        reject_retry(
            code="EMAIL_DELIVERY_NOT_FOUND",
            message="Email delivery not found.",
            outcome="NOT_FOUND",
            validation_result="DELIVERY_NOT_FOUND",
            http_status=404,
        )
    if delivery.status not in {"FAILED", "SKIPPED_UNCONFIGURED"}:
        reject_retry(
            code="EMAIL_DELIVERY_NOT_RETRYABLE",
            message="Only failed or previously unconfigured email deliveries can be retried.",
            outcome="REJECTED",
            validation_result="DELIVERY_STATUS_NOT_RETRYABLE",
            previous_status=delivery.status,
        )

    notification = db.get(Notification, delivery.notification_id)
    if notification is None:
        reject_retry(
            code="NOTIFICATION_NOT_FOUND",
            message="The notification for this delivery no longer exists.",
            outcome="REJECTED",
            validation_result="NOTIFICATION_NOT_FOUND",
            previous_status=delivery.status,
        )
    user = db.get(User, notification.user_id)
    if user is None or not user.email or not user.email_verified:
        reject_retry(
            code="EMAIL_RECIPIENT_NOT_ELIGIBLE",
            message="The recipient does not have a verified email address.",
            outcome="REJECTED",
            validation_result="RECIPIENT_NOT_VERIFIED",
            previous_status=delivery.status,
        )
    preference = db.get(NotificationEmailPreference, user.id)
    if preference is None or not preference.enabled:
        reject_retry(
            code="EMAIL_DELIVERY_DISABLED",
            message="The recipient has email notifications disabled.",
            outcome="REJECTED",
            validation_result="EMAIL_PREFERENCE_DISABLED",
            previous_status=delivery.status,
        )
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_from_email:
        reject_retry(
            code="SMTP_NOT_CONFIGURED",
            message="Configure SMTP before retrying this delivery.",
            outcome="REJECTED",
            validation_result="SMTP_NOT_CONFIGURED",
            previous_status=delivery.status,
        )

    now = datetime.now(timezone.utc)
    notification_id = str(notification.id)
    event = db.scalar(
        select(OutboxEvent)
        .where(
            OutboxEvent.event_type == "NOTIFICATION_EMAIL_REQUESTED",
            OutboxEvent.payload["notification_id"].as_string() == notification_id,
        )
        .order_by(OutboxEvent.created_at.desc(), OutboxEvent.id.desc())
        .limit(1)
        .with_for_update()
    )
    previous_outbox_status = event.status if event is not None else None
    if event is not None and event.status in {"PENDING", "PROCESSING"}:
        reject_retry(
            code="EMAIL_RETRY_ALREADY_QUEUED",
            message="A delivery attempt is already queued or in progress.",
            outcome="CONFLICT",
            validation_result="RETRY_ALREADY_QUEUED",
            previous_status=delivery.status,
        )
    if event is None:
        event = OutboxEvent(
            event_type="NOTIFICATION_EMAIL_REQUESTED",
            aggregate_type="Notification",
            aggregate_id=notification.id,
            payload={"notification_id": notification_id},
            status="PENDING",
            attempt_count=0,
            available_at=now,
            created_at=now,
        )
        db.add(event)
        db.flush()
    else:
        event.status = "PENDING"
        event.attempt_count = 0
        event.available_at = now
        event.locked_at = None
        event.processed_at = None
        event.last_error = None

    previous_delivery_status = delivery.status
    delivery.status = "PENDING"
    delivery.last_error = None
    delivery.updated_at = now
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="NOTIFICATION_EMAIL_RETRY_REQUESTED",
        resource_type="notification_email_delivery",
        resource_id=delivery.id,
        outcome="SUCCESS",
        details={
            "notification_id": notification_id,
            "previous_delivery_status": previous_delivery_status,
            "new_delivery_status": delivery.status,
            "previous_outbox_status": previous_outbox_status,
            "new_outbox_status": event.status,
            "attempt_count_before_retry": delivery.attempt_count,
            "reason": payload.reason,
        },
        request_id=request.headers.get("x-request-id"),
        created_at=now,
    ))
    db.commit()
    return {
        "data": {
            "delivery_id": str(delivery.id),
            "notification_id": notification_id,
            "delivery_status": "PENDING",
            "outbox_status": "PENDING",
            "queued_at": now,
            "delivery_confirmed": False,
        }
    }


@router.get("/integrations/status", response_model=dict)
def get_admin_integration_status(
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Report safe configuration facts separately from observed provider health."""
    _require_admin(db, principal, request)

    from app.config.settings import get_settings
    from app.infrastructure.db.models.notifications import NotificationEmailDelivery

    settings = get_settings()
    storage_provider = (settings.document_storage_provider or "").strip().lower()
    if storage_provider == "r2":
        storage_configured = all((
            settings.r2_bucket,
            settings.r2_endpoint_url,
            settings.r2_access_key_id,
            settings.r2_secret_access_key,
        ))
        storage_state = "CONFIGURED" if storage_configured else "INCOMPLETE"
    elif storage_provider == "gcs":
        storage_configured = bool(settings.google_cloud_storage_bucket)
        storage_state = "CONFIGURED" if storage_configured else "INCOMPLETE"
    else:
        storage_configured = False
        storage_state = "UNSUPPORTED_PROVIDER"

    smtp_configured = bool(settings.smtp_host and settings.smtp_from_email)
    drive_enabled = bool(settings.google_drive_backup_enabled)
    drive_configured = all((
        settings.google_drive_client_id,
        settings.google_drive_client_secret,
        settings.google_drive_refresh_token,
    ))
    if not drive_enabled:
        drive_state = "DISABLED"
    else:
        drive_state = "CONFIGURED" if drive_configured else "INCOMPLETE"

    email_delivery_rows = db.execute(
        select(NotificationEmailDelivery.status, func.count(NotificationEmailDelivery.id))
        .group_by(NotificationEmailDelivery.status)
        .order_by(NotificationEmailDelivery.status)
    ).all()
    latest_email_delivery = db.scalar(
        select(func.max(NotificationEmailDelivery.updated_at))
    )
    latest_source_observation = db.scalar(select(func.max(Source.last_observed_at)))
    latest_observations = {
        key: _latest_integration_observation(db, key)
        for key in (
            "document_storage", "smtp_email", "document_ocr",
            "google_drive_backup", "firebase_identity",
        )
    }

    def observed_fields(key: str) -> dict[str, Any]:
        row = latest_observations[key]
        return {
            "observed_status": row.status if row is not None else "UNKNOWN",
            "last_tested_at": row.observed_at if row is not None else None,
            "last_error_category": row.error_category if row is not None else None,
        }

    return {
        "data": {
            "integrations": [
                {
                    "key": "document_storage",
                    "provider": storage_provider or None,
                    "configuration_state": storage_state,
                    "configured": bool(storage_configured),
                    **observed_fields("document_storage"),
                },
                {
                    "key": "smtp_email",
                    "provider": "SMTP",
                    "configuration_state": "CONFIGURED" if smtp_configured else "INCOMPLETE",
                    "configured": smtp_configured,
                    **observed_fields("smtp_email"),
                },
                {
                    "key": "document_ocr",
                    "provider": "GOOGLE_CLOUD_VISION",
                    "configuration_state": "ENABLED" if settings.document_ocr_enabled else "DISABLED",
                    "configured": None,
                    **observed_fields("document_ocr"),
                },
                {
                    "key": "google_drive_backup",
                    "provider": "GOOGLE_DRIVE",
                    "configuration_state": drive_state,
                    "configured": bool(drive_configured),
                    "enabled": drive_enabled,
                    **observed_fields("google_drive_backup"),
                },
                {
                    "key": "firebase_identity",
                    "provider": "FIREBASE",
                    "configuration_state": "NOT_INTROSPECTED",
                    "configured": None,
                    **observed_fields("firebase_identity"),
                },
            ],
            "source_observations": {
                "operational_state_counts": _status_counts(db, Source, "operational_state"),
                "latest_observed_at": latest_source_observation,
            },
            "email_delivery_observations": {
                "status_counts": {state: count for state, count in email_delivery_rows},
                "latest_updated_at": latest_email_delivery,
                "note": "Persisted delivery outcomes are not a provider-wide health guarantee.",
            },
        }
    }

def _impersonation_session_response(session: AdminImpersonationSession) -> dict[str, Any]:
    return {
        "id": str(session.id),
        "target_user_id": str(session.target_user_id),
        "reason": session.reason,
        "status": session.status,
        "started_at": session.started_at,
        "expires_at": session.expires_at,
        "ended_at": session.ended_at,
        "end_reason": session.end_reason,
        "read_only": True,
    }


def _impersonation_ttl_seconds() -> int:
    raw = os.getenv("JOBPUNTER_ADMIN_IMPERSONATION_TTL_SECONDS", "900")
    try:
        ttl = int(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail="Administrative impersonation expiry is misconfigured.",
        ) from exc
    if not 60 <= ttl <= 3600:
        raise HTTPException(
            status_code=503,
            detail="Administrative impersonation expiry must be between 60 and 3600 seconds.",
        )
    return ttl


@router.post("/impersonation-sessions", status_code=status.HTTP_201_CREATED)
def start_admin_impersonation_session(
    payload: ImpersonationSessionStartRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Start a durable, bounded, read-only user inspection session."""
    _require_admin(db, principal, request)
    if payload.target_user_id == principal.user_id:
        raise HTTPException(status_code=422, detail="Administrators cannot impersonate themselves.")
    target = db.get(User, payload.target_user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Target user not found.")

    now = datetime.now(timezone.utc)
    existing = db.scalar(
        select(AdminImpersonationSession)
        .where(
            AdminImpersonationSession.admin_user_id == principal.user_id,
            AdminImpersonationSession.target_user_id == target.id,
            AdminImpersonationSession.status == "ACTIVE",
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.expires_at <= now:
            existing.status = "EXPIRED"
            existing.ended_at = now
            existing.end_reason = "EXPIRED"
        else:
            raise HTTPException(
                status_code=409,
                detail="An active inspection session already exists for this target.",
            )
        db.flush()

    session = AdminImpersonationSession(
        admin_user_id=principal.user_id,
        target_user_id=target.id,
        reason=payload.reason,
        status="ACTIVE",
        started_at=now,
        expires_at=now + timedelta(seconds=_impersonation_ttl_seconds()),
    )
    db.add(session)
    db.flush()
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="IMPERSONATION_SESSION_STARTED",
        resource_type="admin_impersonation_session",
        resource_id=session.id,
        outcome="SUCCESS",
        details={
            "target_user_id": str(target.id),
            "reason": payload.reason,
            "expires_at": session.expires_at.isoformat(),
            "read_only": True,
        },
        request_id=request.headers.get("x-request-id"),
        created_at=now,
    ))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An active inspection session already exists for this target.",
        ) from exc
    db.refresh(session)
    return {"data": _impersonation_session_response(session)}


@router.get("/impersonation-sessions/{session_id}/workspace")
def read_admin_impersonation_workspace(
    session_id: UUID,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Read the minimized target workspace; this endpoint exposes no mutation operations."""
    _require_admin(db, principal, request)
    session = db.scalar(
        select(AdminImpersonationSession)
        .where(
            AdminImpersonationSession.id == session_id,
            AdminImpersonationSession.admin_user_id == principal.user_id,
        )
        .with_for_update()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Inspection session not found.")
    now = datetime.now(timezone.utc)
    if session.status == "ACTIVE" and session.expires_at <= now:
        session.status = "EXPIRED"
        session.ended_at = now
        session.end_reason = "EXPIRED"
        db.add(AdminAuditEvent(
            actor_user_id=principal.user_id,
            action="IMPERSONATION_SESSION_EXPIRED",
            resource_type="admin_impersonation_session",
            resource_id=session.id,
            outcome="EXPIRED",
            details={"target_user_id": str(session.target_user_id)},
            request_id=request.headers.get("x-request-id"),
            created_at=now,
        ))
        db.commit()
        raise HTTPException(status_code=410, detail="Inspection session has expired.")
    if session.status != "ACTIVE":
        raise HTTPException(status_code=410, detail="Inspection session is no longer active.")

    # Re-run the normal, audited user-detail path on every workspace read. The target is
    # never substituted for the administrator's authenticated principal.
    workspace = get_admin_user_detail(
        user_id=session.target_user_id,
        request=request,
        principal=principal,
        db=db,
    )
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="IMPERSONATION_WORKSPACE_VIEWED",
        resource_type="admin_impersonation_session",
        resource_id=session.id,
        outcome="SUCCESS",
        details={"target_user_id": str(session.target_user_id)},
        request_id=request.headers.get("x-request-id"),
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()
    return {
        "session": _impersonation_session_response(session),
        "workspace": workspace["data"],
    }


@router.post("/impersonation-sessions/{session_id}/end")
def end_admin_impersonation_session(
    session_id: UUID,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """End an inspection session; repeated exit requests are idempotent."""
    _require_admin(db, principal, request)
    session = db.scalar(
        select(AdminImpersonationSession)
        .where(
            AdminImpersonationSession.id == session_id,
            AdminImpersonationSession.admin_user_id == principal.user_id,
        )
        .with_for_update()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Inspection session not found.")

    now = datetime.now(timezone.utc)
    changed = session.status == "ACTIVE"
    if changed:
        expired = session.expires_at <= now
        session.status = "EXPIRED" if expired else "ENDED"
        session.ended_at = now
        session.end_reason = "EXPIRED" if expired else "ADMIN_ENDED"
        db.add(AdminAuditEvent(
            actor_user_id=principal.user_id,
            action="IMPERSONATION_SESSION_EXPIRED" if expired else "IMPERSONATION_SESSION_ENDED",
            resource_type="admin_impersonation_session",
            resource_id=session.id,
            outcome="EXPIRED" if expired else "SUCCESS",
            details={"target_user_id": str(session.target_user_id), "reason": session.end_reason},
            request_id=request.headers.get("x-request-id"),
            created_at=now,
        ))
        db.commit()
        db.refresh(session)
    return {"data": _impersonation_session_response(session), "changed": changed}

def _forbidden_target_rule_response(rule: ForbiddenTargetRule) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "rule_type": rule.rule_type,
        "pattern": rule.pattern,
        "connector_scope": rule.connector_scope,
        "reason": rule.reason,
        "active": rule.active,
        "created_by_user_id": str(rule.created_by_user_id),
        "created_at": rule.created_at,
        "updated_by_user_id": str(rule.updated_by_user_id) if rule.updated_by_user_id else None,
        "updated_at": rule.updated_at,
    }


@router.get("/target-policy/forbidden-rules")
def list_forbidden_target_rules(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    active: bool | None = None,
    rule_type: Literal["HOST_EXACT", "HOST_SUFFIX", "URL_EXACT", "URL_PATH_PREFIX"] | None = None,
    connector_scope: Literal[
        "ALL", "SCRAPY", "PLAYWRIGHT", "JSON_API", "RSS_ATOM", "JOBSPY", "GREENHOUSE_JOB_BOARD"
    ] | None = None,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal, request)
    filters = []
    if active is not None:
        filters.append(ForbiddenTargetRule.active.is_(active))
    if rule_type is not None:
        filters.append(ForbiddenTargetRule.rule_type == rule_type)
    if connector_scope is not None:
        filters.append(ForbiddenTargetRule.connector_scope == connector_scope)
    total = int(db.scalar(
        select(func.count(ForbiddenTargetRule.id)).where(*filters)
    ) or 0)
    rows = db.scalars(
        select(ForbiddenTargetRule).where(*filters)
        .order_by(ForbiddenTargetRule.created_at.desc(), ForbiddenTargetRule.id.desc())
        .limit(limit).offset(offset)
    ).all()
    return {
        "data": [_forbidden_target_rule_response(row) for row in rows],
        "pagination": {"limit": limit, "offset": offset, "total": total},
    }


@router.post("/target-policy/forbidden-rules", status_code=status.HTTP_201_CREATED)
def create_forbidden_target_rule(
    payload: ForbiddenTargetRuleCreateRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal, request)
    try:
        normalized_pattern = normalize_forbidden_rule(payload.rule_type, payload.pattern)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_FORBIDDEN_TARGET_RULE", "message": str(exc)},
        ) from exc

    now = datetime.now(timezone.utc)
    rule = ForbiddenTargetRule(
        rule_type=payload.rule_type,
        pattern=normalized_pattern,
        connector_scope=payload.connector_scope,
        reason=payload.reason,
        active=True,
        created_by_user_id=principal.user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(rule)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A rule with this type, normalized pattern and connector scope already exists.",
        ) from exc
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="FORBIDDEN_TARGET_RULE_CREATED",
        resource_type="forbidden_target_rule",
        resource_id=rule.id,
        outcome="SUCCESS",
        details={
            "rule_type": rule.rule_type,
            "pattern": rule.pattern,
            "connector_scope": rule.connector_scope,
            "active": True,
            "reason": payload.reason,
        },
        request_id=request.headers.get("x-request-id"),
        created_at=now,
    ))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A rule with this type, normalized pattern and connector scope already exists.",
        ) from exc
    db.refresh(rule)
    return {"data": _forbidden_target_rule_response(rule)}


@router.patch("/target-policy/forbidden-rules/{rule_id}/status")
def change_forbidden_target_rule_status(
    rule_id: UUID,
    payload: ForbiddenTargetRuleStatusRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal, request)
    rule = db.scalar(
        select(ForbiddenTargetRule).where(ForbiddenTargetRule.id == rule_id).with_for_update()
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Forbidden-target rule not found.")
    previous = rule.active
    if previous == payload.active:
        return {"data": _forbidden_target_rule_response(rule), "changed": False}

    now = datetime.now(timezone.utc)
    rule.active = payload.active
    rule.updated_by_user_id = principal.user_id
    rule.updated_at = now
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="FORBIDDEN_TARGET_RULE_ACTIVATED" if payload.active else "FORBIDDEN_TARGET_RULE_DEACTIVATED",
        resource_type="forbidden_target_rule",
        resource_id=rule.id,
        outcome="SUCCESS",
        details={
            "rule_type": rule.rule_type,
            "pattern": rule.pattern,
            "connector_scope": rule.connector_scope,
            "previous_active": previous,
            "active": payload.active,
            "reason": payload.reason,
        },
        request_id=request.headers.get("x-request-id"),
        created_at=now,
    ))
    db.commit()
    db.refresh(rule)
    return {"data": _forbidden_target_rule_response(rule), "changed": True}

@router.post("/integrations/{integration_key}/health-check", response_model=dict)
def check_admin_integration_health(
    integration_key: str,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Run a bounded, non-destructive SMTP check and persist its sanitized observation."""
    _require_admin(db, principal, request)
    key = integration_key.strip().lower()
    if key != "smtp_email":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INTEGRATION_HEALTH_CHECK_UNSUPPORTED",
                "message": "No safe live health-check adapter is implemented for this integration.",
            },
        )

    from app.config.settings import get_settings

    settings = get_settings()
    started = time.monotonic()
    observed_at = datetime.now(timezone.utc)
    status_value = "HEALTHY"
    error_category: str | None = None
    details: dict[str, Any] = {
        "connection_established": False,
        "tls_negotiated": False,
        "authentication_configured": bool(settings.smtp_username and settings.smtp_password),
        "email_sent": False,
    }

    if not settings.smtp_host or not settings.smtp_from_email:
        status_value = "UNCONFIGURED"
        error_category = "SMTP_CONFIGURATION_INCOMPLETE"
    elif bool(settings.smtp_username) != bool(settings.smtp_password):
        status_value = "DEGRADED"
        error_category = "SMTP_CREDENTIALS_INCOMPLETE"
    else:
        try:
            with smtplib.SMTP(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout_seconds,
            ) as client:
                code, response_text = client.ehlo()
                if not 200 <= code < 300:
                    raise smtplib.SMTPHeloError(code, response_text)
                details["connection_established"] = True
                if settings.smtp_use_tls:
                    client.starttls(context=ssl.create_default_context())
                    code, response_text = client.ehlo()
                    if not 200 <= code < 300:
                        raise smtplib.SMTPHeloError(code, response_text)
                    details["tls_negotiated"] = True
                if settings.smtp_username and settings.smtp_password:
                    client.login(settings.smtp_username, settings.smtp_password)
        except Exception as exc:
            status_value = "FAILED"
            error_category = type(exc).__name__[:96]

    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    check_type = (
        "CONNECTION_AND_AUTHENTICATION_NO_SEND"
        if settings.smtp_username and settings.smtp_password
        else "CONNECTION_ONLY_NO_SEND"
    )
    observation = IntegrationHealthObservation(
        integration_key=key,
        provider="SMTP",
        status=status_value,
        check_type=check_type,
        observed_at=observed_at,
        duration_ms=duration_ms,
        checked_by_user_id=principal.user_id,
        error_category=error_category,
        details=details,
    )
    db.add(observation)
    db.flush()
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="INTEGRATION_HEALTH_CHECKED",
        resource_type="integration",
        resource_id=observation.id,
        outcome=status_value,
        details={
            "integration_key": key,
            "status": status_value,
            "check_type": observation.check_type,
            "duration_ms": duration_ms,
            "error_category": error_category,
        },
        request_id=request.headers.get("x-request-id"),
        created_at=observed_at,
    ))
    db.commit()
    db.refresh(observation)
    return {"data": {
        "id": str(observation.id),
        "integration_key": observation.integration_key,
        "provider": observation.provider,
        "status": observation.status,
        "check_type": observation.check_type,
        "observed_at": observation.observed_at,
        "duration_ms": observation.duration_ms,
        "checked_by_user_id": str(observation.checked_by_user_id),
        "error_category": observation.error_category,
        "details": observation.details,
    }}


@router.get("/integrations/observations", response_model=dict)
def list_admin_integration_observations(
    request: Request,
    integration_key: str | None = Query(default=None, min_length=2, max_length=64),
    status_filter: Literal["HEALTHY", "DEGRADED", "FAILED", "UNCONFIGURED", "UNSUPPORTED"] | None = Query(default=None, alias="status"),
    observed_after: datetime | None = None,
    observed_before: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal, request)
    observed_after = (
        observed_after.replace(tzinfo=timezone.utc)
        if observed_after is not None and observed_after.tzinfo is None else observed_after
    )
    observed_before = (
        observed_before.replace(tzinfo=timezone.utc)
        if observed_before is not None and observed_before.tzinfo is None else observed_before
    )
    if observed_after is not None and observed_before is not None and observed_after > observed_before:
        raise HTTPException(status_code=422, detail="observed_after must be <= observed_before.")
    filters = []
    if integration_key is not None:
        filters.append(IntegrationHealthObservation.integration_key == integration_key.strip().lower())
    if status_filter is not None:
        filters.append(IntegrationHealthObservation.status == status_filter)
    if observed_after is not None:
        filters.append(IntegrationHealthObservation.observed_at >= observed_after)
    if observed_before is not None:
        filters.append(IntegrationHealthObservation.observed_at <= observed_before)
    total = int(db.scalar(
        select(func.count(IntegrationHealthObservation.id)).where(*filters)
    ) or 0)
    rows = db.scalars(
        select(IntegrationHealthObservation).where(*filters)
        .order_by(
            IntegrationHealthObservation.observed_at.desc(),
            IntegrationHealthObservation.id.desc(),
        ).limit(limit).offset(offset)
    ).all()
    return {
        "data": [{
            "id": str(row.id),
            "integration_key": row.integration_key,
            "provider": row.provider,
            "status": row.status,
            "check_type": row.check_type,
            "observed_at": row.observed_at,
            "duration_ms": row.duration_ms,
            "checked_by_user_id": str(row.checked_by_user_id) if row.checked_by_user_id else None,
            "error_category": row.error_category,
            "details": row.details,
        } for row in rows],
        "pagination": {"limit": limit, "offset": offset, "total": total},
    }



def _discovery_target_response(row: DiscoveryTarget) -> dict[str, Any]:
    return {
        "id": row.id, "source_id": row.source_id,
        "source_connector_id": row.source_connector_id, "url": row.url,
        "target_kind": row.target_kind, "purpose": row.purpose, "status": row.status,
        "validation_status": row.validation_status,
        "validation_error_category": row.validation_error_category,
        "validated_at": row.validated_at,
        "created_by_user_id": row.created_by_user_id, "updated_by_user_id": row.updated_by_user_id,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


@router.get("/discovery-targets", response_model=dict)
def list_discovery_targets(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    source_id: UUID | None = None,
    source_connector_id: UUID | None = None,
    target_kind: Literal["CRAWL", "SEARCH"] | None = None,
    target_status: Literal["DRAFT", "ACTIVE", "PAUSED", "RETIRED"] | None = Query(default=None, alias="status"),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal, request)
    filters = []
    if source_id is not None:
        filters.append(DiscoveryTarget.source_id == source_id)
    if source_connector_id is not None:
        filters.append(DiscoveryTarget.source_connector_id == source_connector_id)
    if target_kind is not None:
        filters.append(DiscoveryTarget.target_kind == target_kind)
    if target_status is not None:
        filters.append(DiscoveryTarget.status == target_status)
    total = int(db.scalar(select(func.count(DiscoveryTarget.id)).where(*filters)) or 0)
    rows = db.scalars(select(DiscoveryTarget).where(*filters)
        .order_by(DiscoveryTarget.created_at.desc(), DiscoveryTarget.id.desc())
        .limit(limit).offset(offset)).all()
    return {"data": [_discovery_target_response(row) for row in rows],
            "pagination": {"limit": limit, "offset": offset, "total": total}}


@router.get("/discovery-targets/{target_id}", response_model=dict)
def get_discovery_target(
    target_id: UUID,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal, request)
    row = db.get(DiscoveryTarget, target_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Discovery target not found.")
    events = db.scalars(select(DiscoveryTargetEvent)
        .where(DiscoveryTargetEvent.target_id == target_id)
        .order_by(DiscoveryTargetEvent.created_at, DiscoveryTargetEvent.id)).all()
    return {
        "data": _discovery_target_response(row),
        "history": [{
            "id": event.id, "actor_user_id": event.actor_user_id, "action": event.action,
            "previous_status": event.previous_status, "new_status": event.new_status,
            "reason": event.reason, "request_id": event.request_id, "created_at": event.created_at,
        } for event in events],
    }


def _record_discovery_target_registration_denial(
    db: Session,
    principal: AuthenticatedPrincipal,
    request: Request,
    *,
    source_id: UUID,
    connector_id: UUID,
    error_code: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Persist a safe audit record for a target registration rejected before a row exists."""
    audit_details: dict[str, Any] = {
        "error_code": error_code,
        "source_id": str(source_id),
        "source_connector_id": str(connector_id),
    }
    if details:
        audit_details.update(details)
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="DISCOVERY_TARGET_REGISTRATION_BLOCKED",
        resource_type="discovery_target_registration",
        resource_id=None,
        outcome="DENIED",
        details=audit_details,
        request_id=request.headers.get("x-request-id"),
        created_at=datetime.now(timezone.utc),
    ))
    db.commit()


@router.post("/discovery-targets", response_model=dict, status_code=status.HTTP_201_CREATED)
def create_discovery_target(
    payload: DiscoveryTargetCreateRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal, request)
    source = db.get(Source, payload.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    connector = db.get(SourceConnector, payload.source_connector_id)
    if connector is None or connector.source_id != source.id:
        raise HTTPException(status_code=422, detail="The selected connector must belong to the selected source.")
    registry = AcquisitionConnectorRegistry()
    if not registry.supports_target(
        connector.connector_type, connector.connector_version, payload.target_kind
    ):
        _record_discovery_target_registration_denial(
            db, principal, request, source_id=source.id, connector_id=connector.id,
            error_code="UNSUPPORTED_DISCOVERY_TARGET_KIND",
            details={
                "target_kind": payload.target_kind,
                "connector_type": connector.connector_type,
                "connector_version": connector.connector_version,
            },
        )
        raise HTTPException(status_code=422, detail={
            "code": "UNSUPPORTED_DISCOVERY_TARGET_KIND",
            "message": "The selected connector implementation does not support this target kind.",
        })
    try:
        normalized_url = normalize_public_target_url(payload.url)
        assert_target_not_forbidden(normalized_url, _active_forbidden_target_rule_payloads(db),
                                    connector_type=connector.connector_type)
    except ForbiddenTargetError as exc:
        _record_discovery_target_registration_denial(
            db, principal, request, source_id=source.id, connector_id=connector.id,
            error_code="FORBIDDEN_TARGET_RULE_MATCHED", details={"rule_id": exc.rule_id},
        )
        raise HTTPException(status_code=409, detail={
            "code": "FORBIDDEN_TARGET_RULE_MATCHED", "rule_id": exc.rule_id,
            "message": "The target is blocked by an active forbidden-target rule.",
        }) from exc
    except ValueError as exc:
        _record_discovery_target_registration_denial(
            db, principal, request, source_id=source.id, connector_id=connector.id,
            error_code="INVALID_DISCOVERY_TARGET_URL",
        )
        raise HTTPException(status_code=422, detail={
            "code": "INVALID_DISCOVERY_TARGET_URL", "message": str(exc),
        }) from exc

    now = datetime.now(timezone.utc)
    row = DiscoveryTarget(
        source_id=source.id, source_connector_id=connector.id, url=normalized_url,
        target_kind=payload.target_kind, purpose=payload.purpose, status="DRAFT",
        validation_status="VALIDATED", validation_error_category=None, validated_at=now,
        created_by_user_id=principal.user_id, created_at=now, updated_at=now,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=(
            "A target with this connector, URL, kind and purpose already exists."
        )) from exc
    db.add(DiscoveryTargetEvent(
        target_id=row.id, actor_user_id=principal.user_id, action="CREATED",
        previous_status=None, new_status="DRAFT", reason="Target registered as a draft.",
        request_id=request.headers.get("x-request-id"), created_at=now,
    ))
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id, action="DISCOVERY_TARGET_CREATED",
        resource_type="discovery_target", resource_id=row.id, outcome="SUCCESS",
        details={"source_id": str(source.id), "source_connector_id": str(connector.id),
                 "url": normalized_url, "target_kind": row.target_kind,
                 "purpose": row.purpose, "status": row.status},
        request_id=request.headers.get("x-request-id"), created_at=now,
    ))
    db.commit()
    db.refresh(row)
    return {"data": _discovery_target_response(row)}


def _record_discovery_target_block(
    db: Session,
    row: DiscoveryTarget,
    principal: AuthenticatedPrincipal,
    request: Request,
    *,
    error_code: str,
    details: dict[str, Any] | None = None,
    validation_error_category: str | None = None,
) -> None:
    """Persist a denied target lifecycle attempt without changing its lifecycle status."""
    now = datetime.now(timezone.utc)
    request_id = request.headers.get("x-request-id")
    if validation_error_category is not None:
        row.validation_status = "BLOCKED"
        row.validation_error_category = validation_error_category
        row.validated_at = now
        row.updated_by_user_id = principal.user_id
        row.updated_at = now
    db.add(DiscoveryTargetEvent(
        target_id=row.id,
        actor_user_id=principal.user_id,
        action="BLOCKED",
        previous_status=row.status,
        new_status=row.status,
        reason=error_code,
        request_id=request_id,
        created_at=now,
    ))
    audit_details: dict[str, Any] = {
        "status": row.status,
        "error_code": error_code,
        "source_id": str(row.source_id),
        "source_connector_id": str(row.source_connector_id),
    }
    if details:
        audit_details.update(details)
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id,
        action="DISCOVERY_TARGET_BLOCKED",
        resource_type="discovery_target",
        resource_id=row.id,
        outcome="DENIED",
        details=audit_details,
        request_id=request_id,
        created_at=now,
    ))
    # A denial is useful only if the history survives the HTTP error response.
    db.commit()


@router.patch("/discovery-targets/{target_id}/status", response_model=dict)
def change_discovery_target_status(
    target_id: UUID,
    payload: DiscoveryTargetStatusRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _require_admin(db, principal, request)
    row = db.scalar(select(DiscoveryTarget).where(DiscoveryTarget.id == target_id).with_for_update())
    if row is None:
        raise HTTPException(status_code=404, detail="Discovery target not found.")
    previous, desired = row.status, payload.status
    if previous == desired:
        return {"data": _discovery_target_response(row), "changed": False}
    allowed = {"DRAFT": {"ACTIVE", "RETIRED"}, "ACTIVE": {"PAUSED", "RETIRED"},
               "PAUSED": {"ACTIVE", "RETIRED"}, "RETIRED": set()}
    if desired not in allowed[previous]:
        _record_discovery_target_block(
            db, row, principal, request,
            error_code="INVALID_DISCOVERY_TARGET_TRANSITION",
        )
        raise HTTPException(status_code=409, detail={
            "code": "INVALID_DISCOVERY_TARGET_TRANSITION",
            "message": f"Cannot change a target from {previous} to {desired}.",
        })
    if desired == "ACTIVE":
        connector = db.get(SourceConnector, row.source_connector_id)
        registry = AcquisitionConnectorRegistry()
        if connector is None or not registry.supports_target(
            connector.connector_type, connector.connector_version, row.target_kind
        ):
            _record_discovery_target_block(
                db, row, principal, request,
                error_code="UNSUPPORTED_DISCOVERY_TARGET_KIND",
                validation_error_category="UNSUPPORTED_DISCOVERY_TARGET_KIND",
                details={
                    "target_kind": row.target_kind,
                    "connector_type": connector.connector_type if connector else None,
                    "connector_version": connector.connector_version if connector else None,
                },
            )
            raise HTTPException(status_code=409, detail={
                "code": "UNSUPPORTED_DISCOVERY_TARGET_KIND",
                "message": "The selected connector implementation does not support this target kind.",
            })
        source = db.get(Source, row.source_id)
        try:
            assert_automated_acquisition_permitted(source)
        except ValueError as exc:
            _record_discovery_target_block(
                db, row, principal, request,
                error_code="SOURCE_PERMISSION_REVIEW_REQUIRED",
            )
            raise HTTPException(status_code=409, detail={
                "code": "SOURCE_PERMISSION_REVIEW_REQUIRED",
                "message": "A target cannot be activated until its source permission review is approved.",
            }) from exc
        try:
            assert_target_not_forbidden(row.url, _active_forbidden_target_rule_payloads(db),
                                        connector_type=connector.connector_type if connector else "")
        except ForbiddenTargetError as exc:
            _record_discovery_target_block(
                db, row, principal, request,
                error_code="FORBIDDEN_TARGET_RULE_MATCHED",
                details={"rule_id": exc.rule_id},
                validation_error_category="FORBIDDEN_TARGET_RULE_MATCHED",
            )
            raise HTTPException(status_code=409, detail={
                "code": "FORBIDDEN_TARGET_RULE_MATCHED", "rule_id": exc.rule_id,
                "message": "The target is blocked by an active forbidden-target rule.",
            }) from exc
        except ValueError as exc:
            _record_discovery_target_block(
                db, row, principal, request,
                error_code="INVALID_DISCOVERY_TARGET_URL",
                validation_error_category="INVALID_DISCOVERY_TARGET_URL",
            )
            raise HTTPException(status_code=422, detail={
                "code": "INVALID_DISCOVERY_TARGET_URL",
                "message": "The target cannot be evaluated safely.",
            }) from exc

    now = datetime.now(timezone.utc)
    if desired == "ACTIVE":
        row.validation_status = "VALIDATED"
        row.validation_error_category = None
        row.validated_at = now
    row.status, row.updated_by_user_id, row.updated_at = desired, principal.user_id, now
    action = {"ACTIVE": "ACTIVATED", "PAUSED": "PAUSED", "RETIRED": "RETIRED"}[desired]
    db.add(DiscoveryTargetEvent(
        target_id=row.id, actor_user_id=principal.user_id, action=action,
        previous_status=previous, new_status=desired, reason=payload.reason,
        request_id=request.headers.get("x-request-id"), created_at=now,
    ))
    db.add(AdminAuditEvent(
        actor_user_id=principal.user_id, action=f"DISCOVERY_TARGET_{action}",
        resource_type="discovery_target", resource_id=row.id, outcome="SUCCESS",
        details={"previous_status": previous, "status": desired, "reason": payload.reason,
                 "source_id": str(row.source_id), "source_connector_id": str(row.source_connector_id)},
        request_id=request.headers.get("x-request-id"), created_at=now,
    ))
    db.commit()
    db.refresh(row)
    return {"data": _discovery_target_response(row), "changed": True}
