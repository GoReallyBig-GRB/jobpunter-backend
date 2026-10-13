import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, RevisionMixin, UUIDPrimaryKeyMixin

class CandidateProfile(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "candidate_profiles"
    __table_args__ = (UniqueConstraint("user_id", name="uq_candidate_profile_user"),)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    profile_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    user: Mapped["User"] = relationship(back_populates="candidate_profile")
    claims: Mapped[list["CandidateClaim"]] = relationship(back_populates="candidate_profile", cascade="all, delete-orphan")
    evidence: Mapped[list["Evidence"]] = relationship(back_populates="candidate_profile", cascade="all, delete-orphan")

class CandidateClaim(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "candidate_claims"
    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_content: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    candidate_profile: Mapped[CandidateProfile] = relationship(back_populates="claims")
    evidence_links: Mapped[list["CandidateClaimEvidence"]] = relationship(back_populates="claim", cascade="all, delete-orphan")

class EvidenceSource(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "evidence_sources"
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_reference: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    evidence: Mapped[list["Evidence"]] = relationship(back_populates="source")

class Evidence(Base, UUIDPrimaryKeyMixin, RevisionMixin):
    __tablename__ = "evidence"
    candidate_profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_profiles.id", ondelete="RESTRICT"), nullable=False)
    evidence_source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence_sources.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    candidate_profile: Mapped[CandidateProfile] = relationship(back_populates="evidence")
    source: Mapped[EvidenceSource] = relationship(back_populates="evidence")
    claim_links: Mapped[list["CandidateClaimEvidence"]] = relationship(back_populates="evidence", cascade="all, delete-orphan")

class CandidateClaimEvidence(Base):
    __tablename__ = "candidate_claim_evidence"
    candidate_claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidate_claims.id", ondelete="CASCADE"), primary_key=True)
    evidence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True)
    relationship_type: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    claim: Mapped[CandidateClaim] = relationship(back_populates="evidence_links")
    evidence: Mapped[Evidence] = relationship(back_populates="claim_links")
