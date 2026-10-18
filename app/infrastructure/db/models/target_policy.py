"""Persisted administrative deny rules for discovery and acquisition targets."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDPrimaryKeyMixin


class ForbiddenTargetRule(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "forbidden_target_rules"
    __table_args__ = (
        CheckConstraint(
            "rule_type IN ('HOST_EXACT', 'HOST_SUFFIX', 'URL_EXACT', 'URL_PATH_PREFIX')",
            name="ck_forbidden_target_rule_type",
        ),
        CheckConstraint(
            "connector_scope IN ('ALL', 'SCRAPY', 'PLAYWRIGHT', 'JSON_API', 'RSS_ATOM', 'JOBSPY', 'GREENHOUSE_JOB_BOARD')",
            name="ck_forbidden_target_connector_scope",
        ),
        UniqueConstraint("rule_type", "pattern", "connector_scope", name="uq_forbidden_target_rule_identity"),
        Index("ix_forbidden_target_rules_active_scope", "active", "connector_scope"),
        Index("ix_forbidden_target_rules_type_pattern", "rule_type", "pattern"),
    )

    rule_type: Mapped[str] = mapped_column(String(24), nullable=False)
    pattern: Mapped[str] = mapped_column(String(2048), nullable=False)
    connector_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="ALL")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
