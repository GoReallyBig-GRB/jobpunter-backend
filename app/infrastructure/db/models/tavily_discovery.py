import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin


class SearchCreditLedgerEntry(Base, UUIDPrimaryKeyMixin):
    """One row per outbound search-provider call: the single source of truth for credit usage.

    A call is recorded before its outcome is acted on, and failed or refused calls are kept, so
    the monthly total and the hard stop can never be understated.
    """

    __tablename__ = "search_credit_ledger"
    __table_args__ = (
        Index("ix_search_credit_ledger_provider_created", "provider", "created_at"),
        CheckConstraint("credits_used >= 0", name="ck_search_credit_ledger_credits"),
        CheckConstraint(
            "outcome IN ('SUCCEEDED', 'FAILED', 'RATE_LIMITED', 'LIMIT_EXCEEDED', 'INVALID_KEY')",
            name="ck_search_credit_ledger_outcome",
        ),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    query_key: Mapped[str] = mapped_column(String(64), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    credits_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_boards_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class DiscoveredAtsBoard(Base, UUIDPrimaryKeyMixin):
    """A company job board found by search and checked against the provider's official API.

    PROBE_OK boards are ready for registration under the owner-approved class permission;
    nothing here fetches jobs.
    """

    __tablename__ = "discovered_ats_boards"
    __table_args__ = (
        UniqueConstraint("provider", "token", name="uq_discovered_ats_board_provider_token"),
        Index("ix_discovered_ats_boards_status", "status", "first_seen_at"),
        CheckConstraint(
            "status IN ('PROBE_OK', 'REJECTED', 'REGISTERED')", name="ck_discovered_ats_board_status",
        ),
    )

    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    token: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(String(64))
    open_jobs_seen: Mapped[int | None] = mapped_column(Integer)
    found_via_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    found_via_query_key: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    last_probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SearchDiscoverySettings(Base, UUIDPrimaryKeyMixin):
    """Administrator overrides for a search provider's discovery schedule (one row per provider).

    A NULL value means "use the deployment default". The hard monthly stop is never overridable here.
    """

    __tablename__ = "search_discovery_settings"
    __table_args__ = (
        UniqueConstraint("provider", name="uq_search_discovery_settings_provider"),
        CheckConstraint(
            "daily_search_cap IS NULL OR daily_search_cap BETWEEN 1 AND 48", name="ck_search_discovery_daily_cap",
        ),
        CheckConstraint(
            "min_minutes_between_searches IS NULL OR min_minutes_between_searches BETWEEN 15 AND 1440",
            name="ck_search_discovery_spacing",
        ),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    daily_search_cap: Mapped[int | None] = mapped_column(Integer)
    min_minutes_between_searches: Mapped[int | None] = mapped_column(Integer)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class SearchDiscoveryTerm(Base, UUIDPrimaryKeyMixin):
    """An administrator-added search input: a company, a free-text query, or an extra sector or market."""

    __tablename__ = "search_discovery_terms"
    __table_args__ = (
        UniqueConstraint("provider", "kind", "normalized_text", name="uq_search_discovery_term"),
        CheckConstraint("kind IN ('COMPANY', 'QUERY', 'SECTOR', 'MARKET')", name="ck_search_discovery_term_kind"),
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str] = mapped_column(String(120), nullable=False)
    normalized_text: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
