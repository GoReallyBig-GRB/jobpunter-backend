"""Candidate-scoped read models for the evidence library."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.errors import DomainError
from app.infrastructure.db.models import (
    CandidateClaimEvidence,
    CandidateProfile,
    CandidateProfileFactProvenance,
    Evidence,
)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def evidence_health_status(evidence: Evidence, now: datetime | None = None) -> tuple[str, str]:
    """Return explicit verification and temporal health labels without inferring facts."""
    now = now or datetime.now(timezone.utc)
    expires_at = _utc(evidence.expires_at)
    effective_from = _utc(evidence.effective_from)
    effective_to = _utc(evidence.effective_to)
    verified_at = _utc(evidence.verified_at)

    if evidence.status == "ARCHIVED":
        health = "ARCHIVED"
    elif evidence.status == "REJECTED":
        health = "REJECTED"
    elif expires_at is not None and expires_at <= now:
        health = "EXPIRED"
    elif effective_from is not None and effective_from > now:
        health = "NOT_YET_EFFECTIVE"
    elif effective_to is not None and effective_to < now:
        health = "OUT_OF_EFFECTIVE_RANGE"
    elif evidence.status != "APPROVED" or verified_at is None:
        health = "UNVERIFIED"
    else:
        health = "CURRENT"

    verification = (
        "VERIFIED"
        if evidence.status == "APPROVED" and verified_at is not None
        else "UNVERIFIED"
    )
    return verification, health


def _payloads(db: Session, records: list[Evidence]) -> list[dict]:
    if not records:
        return []

    evidence_ids = [item.id for item in records]
    profile_ids = {item.candidate_profile_id for item in records}
    provenance = db.scalars(
        select(CandidateProfileFactProvenance)
        .where(
            CandidateProfileFactProvenance.evidence_id.in_(evidence_ids),
            CandidateProfileFactProvenance.candidate_profile_id.in_(profile_ids),
        )
        .order_by(
            CandidateProfileFactProvenance.created_at.asc(),
            CandidateProfileFactProvenance.id.asc(),
        )
    ).all()
    facts_by_evidence: dict[uuid.UUID, list[CandidateProfileFactProvenance]] = {
        evidence_id: [] for evidence_id in evidence_ids
    }
    for record in provenance:
        facts_by_evidence[record.evidence_id].append(record)

    now = datetime.now(timezone.utc)
    result = []
    for evidence in records:
        source = evidence.source
        linked_claims = []
        for link in evidence.claim_links:
            claim = link.claim
            if claim is None or claim.candidate_profile_id != evidence.candidate_profile_id:
                continue
            linked_claims.append({
                "claim_id": claim.id,
                "claim_type": claim.claim_type,
                "claim_status": claim.status,
                "relationship_type": link.relationship_type,
            })

        verification_status, health_status = evidence_health_status(evidence, now)
        result.append({
            "id": evidence.id,
            "candidate_profile_id": evidence.candidate_profile_id,
            "evidence_type": evidence.evidence_type,
            "status": evidence.status,
            "content": evidence.content,
            "revision": evidence.revision,
            "effective_from": evidence.effective_from,
            "effective_to": evidence.effective_to,
            "verified_at": evidence.verified_at,
            "expires_at": evidence.expires_at,
            "created_at": evidence.created_at,
            "updated_at": evidence.updated_at,
            "verification_status": verification_status,
            "health_status": health_status,
            "source": {
                "id": source.id,
                "source_type": source.source_type,
                "source_reference": source.source_reference,
                "source_metadata": source.metadata_json,
                "created_at": source.created_at,
            },
            "linked_claims": linked_claims,
            "linked_profile_facts": [{
                "id": fact.id,
                "section": fact.section,
                "fact_path": fact.fact_path,
                "fact_value": fact.fact_value,
                "profile_revision": fact.profile_revision,
                "source_type": fact.source_type,
                "source_reference": fact.source_reference,
                "source_excerpt": fact.source_excerpt,
                "confidence": fact.confidence,
                "confirmed_at": fact.confirmed_at,
                "created_at": fact.created_at,
            } for fact in facts_by_evidence[evidence.id]],
        })
    return result


def list_evidence_library(
    db: Session,
    user_id: uuid.UUID,
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    evidence_type: str | None = None,
) -> tuple[list[dict], int]:
    profile_id = db.scalar(
        select(CandidateProfile.id).where(CandidateProfile.user_id == user_id)
    )
    if profile_id is None:
        return [], 0

    filters = [Evidence.candidate_profile_id == profile_id]
    if status is not None:
        filters.append(Evidence.status == status)
    if evidence_type is not None:
        filters.append(Evidence.evidence_type == evidence_type)

    total = db.scalar(select(func.count()).select_from(Evidence).where(*filters)) or 0
    records = list(db.scalars(
        select(Evidence)
        .options(
            selectinload(Evidence.source),
            selectinload(Evidence.claim_links).selectinload(CandidateClaimEvidence.claim),
        )
        .where(*filters)
        .order_by(Evidence.created_at.desc(), Evidence.id.desc())
        .offset(offset)
        .limit(limit)
    ).all())
    return _payloads(db, records), total


def get_evidence_library_item(db: Session, user_id: uuid.UUID, evidence_id: uuid.UUID) -> dict:
    evidence = db.scalar(
        select(Evidence)
        .join(CandidateProfile, CandidateProfile.id == Evidence.candidate_profile_id)
        .options(
            selectinload(Evidence.source),
            selectinload(Evidence.claim_links).selectinload(CandidateClaimEvidence.claim),
        )
        .where(
            Evidence.id == evidence_id,
            CandidateProfile.user_id == user_id,
        )
    )
    if evidence is None:
        raise DomainError("NOT_FOUND", "Evidence not found", 404)
    return _payloads(db, [evidence])[0]
