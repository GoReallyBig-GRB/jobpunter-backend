"""System-initiated company discovery through Tavily, bounded by a credit ledger.

One discovery step = at most one search. The step records the call in the ledger, extracts ATS
board tokens from result URLs, probes each new token against the provider's official public API
and stores the outcome in ``discovered_ats_boards``. Nothing here registers a source or fetches
jobs; registration stays behind the owner-approved class permission path.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.jobs import ats_boards
from app.config.settings import get_settings
from app.infrastructure.db.models import (
    DiscoveredAtsBoard, SearchCreditLedgerEntry, SearchDiscoverySettings, SearchDiscoveryTerm, SourceConnector,
)
from app.infrastructure.search import tavily

PROVIDER = "tavily"
PURPOSE = "COMPANY_DISCOVERY"
MAX_PROBES_PER_STEP = 6  # keeps a step well inside the 60 s worker timeout (probe timeout is 8 s)
RETIRE_AFTER_ZERO_YIELD_RUNS = 3

# ATS host -> provider. Only these hosts are searched, so every result is a public board page.
ATS_HOSTS: dict[str, str] = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "boards.eu.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.eu.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
    "apply.workable.com": "workable",
}
INCLUDE_DOMAINS = sorted(ATS_HOSTS)
_RESERVED_SEGMENTS = {"embed", "j", "api", "v1", "jobs", "search", "login", "static", "assets"}

SECTORS = (
    "software engineering", "customer support", "sales", "marketing", "finance and accounting",
    "human resources", "operations and logistics", "banking and fintech", "healthcare", "retail",
    "telecoms", "manufacturing", "education", "consulting", "design and product",
    "oil and gas", "agriculture and agribusiness", "consumer goods", "real estate and construction",
    "hospitality and tourism", "media and entertainment", "legal and compliance",
    "procurement and supply chain", "engineering", "data and analytics", "administration and office work",
)
# Nigerian and wider-African markets, and international markets. The plan interleaves the two so every
# day of searching covers both local employers and international ones.
LOCAL_MARKETS = ("Nigeria", "Lagos", "Abuja", "Port Harcourt", "Ibadan")
INTERNATIONAL_MARKETS = (
    "remote worldwide", "United Kingdom", "United States", "Canada", "Germany", "Australia",
    "India", "United Arab Emirates", "South Africa", "Kenya", "Ghana",
)
MARKETS = LOCAL_MARKETS + INTERNATIONAL_MARKETS


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _plan_texts() -> list[str]:
    texts: list[str] = []
    for sector in SECTORS:
        order: list[str] = []
        for index in range(max(len(LOCAL_MARKETS), len(INTERNATIONAL_MARKETS))):
            if index < len(LOCAL_MARKETS):
                order.append(LOCAL_MARKETS[index])
            if index < len(INTERNATIONAL_MARKETS):
                order.append(INTERNATIONAL_MARKETS[index])
        texts.extend(f"{sector} jobs {market}" for market in order)
    return texts


QUERY_PLAN: tuple[tuple[str, str], ...] = tuple((_key(text), text) for text in _plan_texts())


MAX_ACTIVE_TERMS = 200
MAX_DAILY_CAP = 48
MIN_SPACING_MINUTES = 15
TERM_KINDS = ("COMPANY", "QUERY", "SECTOR", "MARKET")
_TERM_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &.,'/()\-]{1,119}$")


@dataclass(frozen=True)
class EffectiveSettings:
    enabled: bool
    daily_search_cap: int
    min_minutes_between_searches: int
    overridden: bool


def max_allowed_daily_cap() -> int:
    """Largest daily cap whose 31-day total still fits under the monthly hard stop."""
    s = get_settings()
    return min(MAX_DAILY_CAP, int(s.tavily_monthly_credit_limit * s.tavily_hard_stop_fraction) // 31)


def effective_settings(db: Session) -> EffectiveSettings:
    s = get_settings()
    row = db.scalar(select(SearchDiscoverySettings).where(SearchDiscoverySettings.provider == PROVIDER))
    if row is None:
        return EffectiveSettings(True, s.tavily_daily_search_cap, s.tavily_min_minutes_between_searches, False)
    return EffectiveSettings(
        row.enabled,
        row.daily_search_cap or s.tavily_daily_search_cap,
        row.min_minutes_between_searches or s.tavily_min_minutes_between_searches,
        row.daily_search_cap is not None or row.min_minutes_between_searches is not None or not row.enabled,
    )


def normalize_term(kind: str, raw: str) -> str:
    """Validate an administrator-supplied term. It is sent to the search provider, so it must be a
    plain company, sector, place or phrase: no addresses, links or personal markers."""
    if kind not in TERM_KINDS:
        raise ValueError("kind must be one of " + ", ".join(TERM_KINDS))
    text = " ".join(str(raw).split())
    lowered = text.lower()
    if "@" in text or "http" in lowered or "://" in text or "www." in lowered:
        raise ValueError("text must be a plain name or phrase, not an address or link")
    if not _TERM_PATTERN.match(text):
        raise ValueError("text must be 2-120 characters: letters, numbers, spaces and & . , ' / ( ) -")
    return text


def _active_terms(db: Session) -> list[SearchDiscoveryTerm]:
    return list(db.scalars(
        select(SearchDiscoveryTerm).where(
            SearchDiscoveryTerm.provider == PROVIDER, SearchDiscoveryTerm.active.is_(True)
        ).order_by(SearchDiscoveryTerm.created_at, SearchDiscoveryTerm.id)
    ))


def priority_queries(terms: list[SearchDiscoveryTerm]) -> list[str]:
    """Company and free-text terms: each is searched once, ahead of the rotation."""
    out: list[str] = []
    for t in terms:
        if t.kind == "COMPANY":
            out.append(f"{t.text} careers jobs")
        elif t.kind == "QUERY":
            out.append(t.text)
    return out


def rotation_texts(terms: list[SearchDiscoveryTerm]) -> list[str]:
    """The built-in plan plus every sector x market pair opened up by added sectors and markets."""
    base = _plan_texts()
    sectors = list(SECTORS) + [t.text for t in terms if t.kind == "SECTOR"]
    markets = list(MARKETS) + [t.text for t in terms if t.kind == "MARKET"]
    seen = set(base)
    extra: list[str] = []
    if len(sectors) > len(SECTORS) or len(markets) > len(MARKETS):
        for sector in sectors:
            for market in markets:
                text = f"{sector} jobs {market}"
                if text not in seen:
                    seen.add(text)
                    extra.append(text)
    return base + extra


@dataclass(frozen=True)
class BudgetStatus:
    month_credits_used: int
    monthly_limit: int
    hard_stop_credits: int
    searches_today: int
    daily_cap: int
    last_call_at: datetime | None
    blocked_reason: str | None

    @property
    def can_search(self) -> bool:
        return self.blocked_reason is None


def _month_start(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def budget_status(db: Session, now: datetime | None = None) -> BudgetStatus:
    now = now or datetime.now(timezone.utc)
    s = get_settings()
    eff = effective_settings(db)
    base = [SearchCreditLedgerEntry.provider == PROVIDER]
    used = int(db.scalar(select(func.coalesce(func.sum(SearchCreditLedgerEntry.credits_used), 0)).where(
        *base, SearchCreditLedgerEntry.created_at >= _month_start(now))) or 0)
    day_start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today = int(db.scalar(select(func.count()).select_from(SearchCreditLedgerEntry).where(
        *base, SearchCreditLedgerEntry.created_at >= day_start)) or 0)
    last = db.scalar(select(func.max(SearchCreditLedgerEntry.created_at)).where(*base))
    hard_stop = int(s.tavily_monthly_credit_limit * s.tavily_hard_stop_fraction)
    reason = None
    # The provider's own refusals are honoured first: they mean our local count is behind reality.
    refusal = db.scalar(select(SearchCreditLedgerEntry).where(
        *base, SearchCreditLedgerEntry.outcome.in_(("LIMIT_EXCEEDED", "INVALID_KEY")),
        SearchCreditLedgerEntry.created_at >= now - timedelta(hours=24),
    ).order_by(SearchCreditLedgerEntry.created_at.desc()).limit(1))
    if not eff.enabled:
        reason = "PAUSED_BY_ADMIN"
    elif refusal is not None:
        reason = "PROVIDER_LIMIT_OR_INVALID_KEY"
    elif used >= hard_stop:
        reason = "MONTHLY_HARD_STOP"
    elif today >= eff.daily_search_cap:
        reason = "DAILY_CAP"
    elif last is not None and now - last.astimezone(timezone.utc) < timedelta(
        minutes=eff.min_minutes_between_searches
    ):
        reason = "TOO_SOON"
    return BudgetStatus(used, s.tavily_monthly_credit_limit, hard_stop, today, eff.daily_search_cap, last, reason)


def extract_board(url: str) -> tuple[str, str] | None:
    """(provider, token) for an ATS board or job URL, else None. Never fetches anything."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    provider = ATS_HOSTS.get((parsed.hostname or "").lower())
    if provider is None:
        return None
    segments = [seg for seg in parsed.path.split("/") if seg]
    if not segments:
        return None
    token = segments[0]
    if token.lower() in _RESERVED_SEGMENTS or not ats_boards.valid_token(token):
        return None
    return provider, token


def _registered_boards(db: Session) -> set[tuple[str, str]]:
    """(provider, lower token) of every board already registered as a source connector."""
    found: set[tuple[str, str]] = set()
    for connector_type, config in db.execute(select(SourceConnector.connector_type, SourceConnector.configuration)):
        config = config or {}
        if connector_type == "GREENHOUSE_JOB_BOARD" and isinstance(config.get("board_token"), str):
            found.add(("greenhouse", config["board_token"].lower()))
        endpoint = config.get("endpoint_url")
        if isinstance(endpoint, str):
            for provider in ("lever", "ashby", "workable"):
                match = re.match(
                    re.escape(ats_boards.PROVIDERS[provider]["endpoint"].split("{token}")[0]) + r"([^/?#]+)", endpoint,
                )
                if match:
                    found.add((provider, match.group(1).lower()))
    return found


def next_query(db: Session) -> tuple[str, str] | None:
    """Administrator-added companies and queries first (once each), then the rotation, skipping
    queries retired for repeated zero yield."""
    runs = db.execute(
        select(SearchCreditLedgerEntry.query_key, SearchCreditLedgerEntry.new_boards_count)
        .where(SearchCreditLedgerEntry.provider == PROVIDER, SearchCreditLedgerEntry.outcome == "SUCCEEDED")
        .order_by(SearchCreditLedgerEntry.created_at.desc())
    ).all()
    history: dict[str, list[int]] = {}
    for key, new_boards in runs:
        history.setdefault(key, []).append(new_boards)
    terms = _active_terms(db)
    for text in priority_queries(terms):
        if _key(text) not in history:
            return _key(text), text
    retired = {
        key for key, yields in history.items()
        if len(yields) >= RETIRE_AFTER_ZERO_YIELD_RUNS and not any(yields[:RETIRE_AFTER_ZERO_YIELD_RUNS])
    }
    live = [(_key(t), t) for t in rotation_texts(terms) if _key(t) not in retired]
    if not live:
        return None
    # runs are newest first, so the first position of a key is how recently it was searched.
    recency: dict[str, int] = {}
    for position, (key, _new) in enumerate(runs):
        recency.setdefault(key, position)
    never = [item for item in live if item[0] not in recency]
    if never:
        return never[0]
    return max(live, key=lambda item: recency[item[0]])  # least recently searched


@dataclass(frozen=True)
class StepResult:
    status: str  # SKIPPED_<reason>, NO_QUERY, SEARCHED
    outcome: str | None = None
    credits: int = 0
    urls: int = 0
    new_boards: int = 0
    probe_ok: int = 0
    rejected: int = 0


def run_discovery_step(
    db: Session, *, now: datetime | None = None,
    search_fn: Callable[..., tavily.TavilyResult] = tavily.search,
    probe_fn: Callable[[str, str], ats_boards.ProbeResult] = ats_boards.probe_board,
) -> StepResult:
    """Perform at most one budgeted search and record everything it found."""
    now = now or datetime.now(timezone.utc)
    budget = budget_status(db, now)
    if not budget.can_search:
        return StepResult(f"SKIPPED_{budget.blocked_reason}")
    query = next_query(db)
    if query is None:
        return StepResult("NO_QUERY")
    key, text = query
    # Reserve the slot first so a crash or timeout after the call can never allow an unrecorded repeat.
    entry = SearchCreditLedgerEntry(
        provider=PROVIDER, purpose=PURPOSE, query_key=key, query_text=text, credits_used=0,
        outcome="FAILED", created_at=now,
    )
    db.add(entry)
    db.commit()
    result = search_fn(text, INCLUDE_DOMAINS, max_results=20)
    entry.outcome = result.outcome
    entry.http_status = result.http_status
    entry.provider_request_id = result.request_id
    if result.ok:
        entry.credits_used = result.credits
    elif result.outcome == "FAILED":
        entry.credits_used = 1  # unknown whether billed: count it so usage is never understated
    entry.result_count = len(result.urls)
    db.commit()
    if not result.ok:
        return StepResult("SEARCHED", outcome=result.outcome, credits=entry.credits_used)

    known = _registered_boards(db)
    seen_db = {(p, t.lower()) for p, t in db.execute(select(DiscoveredAtsBoard.provider, DiscoveredAtsBoard.token))}
    fresh: dict[tuple[str, str], tuple[str, str]] = {}
    for url in result.urls:
        board = extract_board(url)
        if board is None:
            continue
        ident = (board[0], board[1].lower())
        if ident in known or ident in seen_db or ident in fresh:
            continue
        fresh[ident] = (board[1], url)
    probe_ok = rejected = 0
    for (provider, _lower), (token, url) in list(fresh.items())[:MAX_PROBES_PER_STEP]:
        probe = probe_fn(provider, token)
        db.add(DiscoveredAtsBoard(
            provider=provider, token=token, status="PROBE_OK" if probe.ok else "REJECTED",
            reject_reason=None if probe.ok else (probe.reason or "PROBE_FAILED")[:64],
            open_jobs_seen=probe.job_count if probe.ok else None,
            found_via_url=url[:2048], found_via_query_key=key, first_seen_at=now, last_probed_at=now,
        ))
        probe_ok += 1 if probe.ok else 0
        rejected += 0 if probe.ok else 1
    entry.new_boards_count = probe_ok
    db.commit()
    return StepResult(
        "SEARCHED", outcome="SUCCEEDED", credits=entry.credits_used, urls=len(result.urls),
        new_boards=len(fresh), probe_ok=probe_ok, rejected=rejected,
    )
