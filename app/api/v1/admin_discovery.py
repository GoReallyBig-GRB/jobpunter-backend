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
    discovery_candidate_is_executable,
    intake_discovered_urls,
    invalidate_discovery_candidate_approval,
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


@router.get("/tavily")
def tavily_discovery_status(
    request: Request, principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    """Truthful search budget, usage and yield for system-initiated Tavily company discovery.

    ``daily`` and ``monthly`` come from our own ledger (every call we made); ``provider_usage`` is
    Tavily's own count, so a difference between the two is visible instead of hidden.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func

    from app.application.jobs import tavily_discovery as td
    from app.config.settings import get_settings
    from app.infrastructure.db.models import DiscoveredAtsBoard, SearchCreditLedgerEntry
    from app.infrastructure.search import tavily

    _require_admin(db, principal, request)
    now = datetime.now(timezone.utc)
    budget = td.budget_status(db, now)
    configured = tavily.api_key() is not None
    ledger = SearchCreditLedgerEntry
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = td._month_start(now)
    today_rows = db.execute(
        select(ledger.outcome, func.count(), func.coalesce(func.sum(ledger.new_boards_count), 0))
        .where(ledger.provider == td.PROVIDER, ledger.created_at >= day_start).group_by(ledger.outcome)
    ).all()
    outcomes_today = {outcome: int(count) for outcome, count, _new in today_rows}
    month_searches, month_new = db.execute(
        select(func.count(), func.coalesce(func.sum(ledger.new_boards_count), 0))
        .where(ledger.provider == td.PROVIDER, ledger.created_at >= month_start)
    ).one()
    month_succeeded = int(db.scalar(select(func.count()).where(
        ledger.provider == td.PROVIDER, ledger.created_at >= month_start, ledger.outcome == "SUCCEEDED")) or 0)
    week_start = day_start - timedelta(days=6)
    day_col = func.date_trunc("day", ledger.created_at)
    by_day = db.execute(
        select(day_col, func.count(), func.coalesce(func.sum(ledger.credits_used), 0),
               func.coalesce(func.sum(ledger.new_boards_count), 0))
        .where(ledger.provider == td.PROVIDER, ledger.created_at >= week_start)
        .group_by(day_col).order_by(day_col.desc())
    ).all()
    boards = {k: int(v) for k, v in db.execute(
        select(DiscoveredAtsBoard.status, func.count()).group_by(DiscoveredAtsBoard.status)).all()}
    by_provider = {k: int(v) for k, v in db.execute(
        select(DiscoveredAtsBoard.provider, func.count()).group_by(DiscoveredAtsBoard.provider)).all()}
    recent = db.scalars(
        select(ledger).where(ledger.provider == td.PROVIDER).order_by(ledger.created_at.desc()).limit(20)
    ).all()
    next_at = None
    if budget.last_call_at is not None:
        from app.config.settings import get_settings
        next_at = (budget.last_call_at + timedelta(
            minutes=get_settings().tavily_min_minutes_between_searches)).isoformat()
    remaining_today = max(0, budget.daily_cap - budget.searches_today)
    eff = td.effective_settings(db)
    defaults = get_settings()
    searched_keys = {
        k: at for k, at in db.execute(
            select(ledger.query_key, func.max(ledger.created_at))
            .where(ledger.provider == td.PROVIDER, ledger.outcome == "SUCCEEDED").group_by(ledger.query_key))
    }
    from app.infrastructure.db.models import SearchDiscoveryTerm
    term_rows = db.scalars(
        select(SearchDiscoveryTerm).where(SearchDiscoveryTerm.provider == td.PROVIDER)
        .order_by(SearchDiscoveryTerm.created_at.desc()).limit(200)
    ).all()

    def _term_view(t) -> dict:
        text = td.priority_queries([t])[0] if t.kind in ("COMPANY", "QUERY") else None
        searched = searched_keys.get(td._key(text)) if text else None
        return {
            "id": str(t.id), "kind": t.kind, "text": t.text, "active": t.active,
            "added_at": t.created_at.isoformat(),
            "searched_at": searched.isoformat() if searched else None,
            "status": ("INACTIVE" if not t.active else
                       ("SEARCHED" if searched else "WAITING") if text else "IN_ROTATION"),
        }

    return {"data": {
        "controls": {
            "enabled": eff.enabled, "daily_search_cap": eff.daily_search_cap,
            "min_minutes_between_searches": eff.min_minutes_between_searches,
            "overridden_by_admin": eff.overridden,
            "default_daily_search_cap": defaults.tavily_daily_search_cap,
            "default_min_minutes_between_searches": defaults.tavily_min_minutes_between_searches,
            "max_daily_search_cap_allowed": td.max_allowed_daily_cap(),
            "min_minutes_between_searches_allowed": td.MIN_SPACING_MINUTES,
        },
        "terms": [_term_view(t) for t in term_rows],
        "api_key_configured": configured,
        "can_search_now": configured and budget.can_search,
        "blocked_reason": "NO_API_KEY" if not configured else budget.blocked_reason,
        "daily": {
            "limit": budget.daily_cap, "searches_done": budget.searches_today,
            "searches_remaining": remaining_today, "outcomes_today": outcomes_today,
            "earliest_next_search_at": next_at,
        },
        "monthly": {
            "credit_limit": budget.monthly_limit, "hard_stop_credits": budget.hard_stop_credits,
            "credits_used": budget.month_credits_used,
            "credits_remaining_before_hard_stop": max(0, budget.hard_stop_credits - budget.month_credits_used),
            "searches": int(month_searches), "searches_succeeded": month_succeeded,
            "new_boards_found": int(month_new),
            "boards_per_credit": round(int(month_new) / budget.month_credits_used, 2)
            if budget.month_credits_used else None,
        },
        "provider_usage": tavily.fetch_usage() if configured else {"available": False, "error": "NO_API_KEY"},
        "last_seven_days": [
            {"day": d.date().isoformat(), "searches": int(c), "credits": int(cr), "new_boards": int(nb)}
            for d, c, cr, nb in by_day
        ],
        "discovered_boards": boards,
        "discovered_boards_by_provider": by_provider,
        "query_plan_size": len(td.QUERY_PLAN),
        "last_call_at": budget.last_call_at.isoformat() if budget.last_call_at else None,
        "recent_calls": [
            {
                "at": e.created_at.isoformat(), "query": e.query_text, "outcome": e.outcome,
                "http_status": e.http_status, "credits": e.credits_used, "results": e.result_count,
                "new_boards": e.new_boards_count,
            }
            for e in recent
        ],
    }}


class TavilySettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    daily_search_cap: int | None = Field(default=None, ge=1, le=48)
    min_minutes_between_searches: int | None = Field(default=None, ge=15, le=1440)
    use_defaults: bool = False
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reason must not be blank")
        return value


class TavilyTermRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["COMPANY", "QUERY", "SECTOR", "MARKET"]
    text: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=3, max_length=1000)


class TavilyTermActiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool
    reason: str = Field(min_length=3, max_length=1000)


@router.patch("/tavily/settings")
def change_tavily_settings(
    payload: TavilySettingsRequest, request: Request,
    principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    """Pause or resume the hourly search and regulate its frequency.

    The daily cap cannot exceed what fits under the monthly hard stop, and the monthly hard stop
    itself is not adjustable here.
    """
    from app.application.jobs import tavily_discovery as td
    from app.infrastructure.db.models import SearchDiscoverySettings

    _require_admin(db, principal, request)
    if payload.use_defaults and any(
        v is not None for v in (payload.enabled, payload.daily_search_cap, payload.min_minutes_between_searches)
    ):
        raise HTTPException(status_code=422, detail="use_defaults cannot be combined with other fields.")
    if not payload.use_defaults and all(
        v is None for v in (payload.enabled, payload.daily_search_cap, payload.min_minutes_between_searches)
    ):
        raise HTTPException(status_code=422, detail="Provide at least one setting to change.")
    ceiling = td.max_allowed_daily_cap()
    if payload.daily_search_cap is not None and payload.daily_search_cap > ceiling:
        raise HTTPException(
            status_code=422,
            detail=f"daily_search_cap above {ceiling} would exceed the monthly hard stop over a 31-day month.",
        )
    row = db.scalar(select(SearchDiscoverySettings).where(
        SearchDiscoverySettings.provider == td.PROVIDER).with_for_update())
    before = td.effective_settings(db)
    if row is None:
        row = SearchDiscoverySettings(provider=td.PROVIDER, enabled=True)
        db.add(row)
    if payload.use_defaults:
        row.enabled, row.daily_search_cap, row.min_minutes_between_searches = True, None, None
    else:
        if payload.enabled is not None:
            row.enabled = payload.enabled
        if payload.daily_search_cap is not None:
            row.daily_search_cap = payload.daily_search_cap
        if payload.min_minutes_between_searches is not None:
            row.min_minutes_between_searches = payload.min_minutes_between_searches
    row.updated_by_user_id = principal.user_id
    row.updated_at = datetime.now(UTC)
    db.flush()
    after = td.effective_settings(db)
    _audit(db, principal.user_id, "TAVILY_DISCOVERY_SETTINGS_CHANGED", row.id, request, {
        "before": {"enabled": before.enabled, "daily_search_cap": before.daily_search_cap,
                   "min_minutes_between_searches": before.min_minutes_between_searches},
        "after": {"enabled": after.enabled, "daily_search_cap": after.daily_search_cap,
                  "min_minutes_between_searches": after.min_minutes_between_searches},
        "reason": payload.reason,
    }, resource_type="search_discovery_settings")
    db.commit()
    return {"data": {
        "enabled": after.enabled, "daily_search_cap": after.daily_search_cap,
        "min_minutes_between_searches": after.min_minutes_between_searches,
        "overridden_by_admin": after.overridden,
    }}


@router.post("/tavily/terms", status_code=201)
def add_tavily_term(
    payload: TavilyTermRequest, request: Request,
    principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    """Add a company, free-text query, sector or market to the hourly search.

    Companies and queries are searched once, ahead of the rotation; sectors and markets join it.
    """
    from sqlalchemy import func

    from app.application.jobs import tavily_discovery as td
    from app.infrastructure.db.models import SearchDiscoveryTerm

    _require_admin(db, principal, request)
    try:
        text = td.normalize_term(payload.kind, payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    active = int(db.scalar(select(func.count()).select_from(SearchDiscoveryTerm).where(
        SearchDiscoveryTerm.provider == td.PROVIDER, SearchDiscoveryTerm.active.is_(True))) or 0)
    if active >= td.MAX_ACTIVE_TERMS:
        raise HTTPException(status_code=409, detail=f"At most {td.MAX_ACTIVE_TERMS} active terms are allowed.")
    existing = db.scalar(select(SearchDiscoveryTerm).where(
        SearchDiscoveryTerm.provider == td.PROVIDER, SearchDiscoveryTerm.kind == payload.kind,
        SearchDiscoveryTerm.normalized_text == text.lower()))
    if existing is not None:
        raise HTTPException(status_code=409, detail="This term already exists." + (
            "" if existing.active else " It is inactive; reactivate it instead."))
    term = SearchDiscoveryTerm(
        provider=td.PROVIDER, kind=payload.kind, text=text, normalized_text=text.lower(), active=True,
        created_by_user_id=principal.user_id,
    )
    db.add(term)
    db.flush()
    _audit(db, principal.user_id, "TAVILY_DISCOVERY_TERM_ADDED", term.id, request,
           {"kind": payload.kind, "text": text, "reason": payload.reason.strip()},
           resource_type="search_discovery_term")
    db.commit()
    return {"data": {"id": str(term.id), "kind": term.kind, "text": term.text, "active": True}}


@router.patch("/tavily/terms/{term_id}")
def change_tavily_term(
    term_id: UUID, payload: TavilyTermActiveRequest, request: Request,
    principal: AuthenticatedPrincipal = PRINCIPAL_DEPENDENCY, db: Session = DB_DEPENDENCY,
) -> dict:
    """Deactivate or reactivate a term. Terms are never deleted so the history stays auditable."""
    from app.application.jobs import tavily_discovery as td
    from app.infrastructure.db.models import SearchDiscoveryTerm

    _require_admin(db, principal, request)
    term = db.scalar(select(SearchDiscoveryTerm).where(
        SearchDiscoveryTerm.id == term_id, SearchDiscoveryTerm.provider == td.PROVIDER).with_for_update())
    if term is None:
        raise HTTPException(status_code=404, detail="Search term not found.")
    previous = term.active
    term.active = payload.active
    term.deactivated_at = None if payload.active else datetime.now(UTC)
    _audit(db, principal.user_id, "TAVILY_DISCOVERY_TERM_CHANGED", term.id, request,
           {"from_active": previous, "to_active": payload.active, "text": term.text,
            "reason": payload.reason.strip()}, resource_type="search_discovery_term")
    db.commit()
    return {"data": {"id": str(term.id), "kind": term.kind, "text": term.text, "active": term.active}}


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
    if not discovery_candidate_is_executable(profile, candidate):
        invalidate_discovery_candidate_approval(candidate)
        candidate.last_error_category = "CANDIDATE_GOVERNANCE_REVIEW_REQUIRED"
        candidate.revision += 1
        db.commit()
        raise HTTPException(
            status_code=409,
            detail="Candidate approval is no longer valid and requires review.",
        )
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
    db.refresh(candidate)
    db.refresh(profile)
    acquired_count = sum(item.acquired_count for item in result.attempts)
    governance_valid = discovery_candidate_is_executable(profile, candidate)
    if result.selected_route is not None and acquired_count > 0 and governance_valid:
        mark_discovery_candidate_acquired(db, candidate)
    elif not governance_valid and candidate.status in {"APPROVED", "PROCESSING"}:
        invalidate_discovery_candidate_approval(candidate)
        candidate.last_error_category = "CANDIDATE_GOVERNANCE_CHANGED"
        candidate.revision += 1
        db.flush()
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
