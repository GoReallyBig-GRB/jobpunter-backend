"""Candidate-scoped read models for the evidence library."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.errors import DomainError
from app.application.candidates.schemas import EvidenceUpdate
from app.infrastructure.db.models import (
    Application,
    ApplicationPackage,
    ApplicationPackageItem,
    AuditEvent,
    CandidateClaim,
    CandidateClaimEvidence,
    CandidateProfile,
    CandidateProfileFactProvenance,
    DocumentImport,
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

    imports = db.scalars(
        select(DocumentImport)
        .where(
            DocumentImport.saved_evidence_id.in_(evidence_ids),
            DocumentImport.candidate_profile_id.in_(profile_ids),
        )
        .order_by(DocumentImport.created_at.desc(), DocumentImport.id.desc())
    ).all()
    import_by_evidence = {}
    for record in imports:
        import_by_evidence.setdefault(record.saved_evidence_id, record)

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
        import_record = import_by_evidence.get(evidence.id)
        conflicts = import_record.conflicts if import_record is not None else []
        extracted_fields = import_record.extracted_fields if import_record is not None else {}
        unresolved = (
            extracted_fields.get("unresolved_items", [])
            if isinstance(extracted_fields, dict)
            else []
        )
        conflict_review_status = (
            "NOT_ASSESSED" if import_record is None
            else "REVIEW_REQUIRED" if conflicts
            else "NONE_REPORTED"
        )
        missing_information_status = (
            "NOT_ASSESSED" if import_record is None
            else "REVIEW_REQUIRED" if unresolved
            else "NONE_REPORTED"
        )
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
            "conflict_review_status": conflict_review_status,
            "conflicts": conflicts or [],
            "missing_information_status": missing_information_status,
            "missing_information": unresolved or [],
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


def _contains_reference(value, target: str) -> bool:
    if isinstance(value, str):
        return value == target
    if isinstance(value, list):
        return any(_contains_reference(item, target) for item in value)
    if isinstance(value, dict):
        return any(_contains_reference(item, target) for item in value.values())
    return False


def update_evidence_library_item(
    db: Session,
    user_id: uuid.UUID,
    evidence_id: uuid.UUID,
    payload: EvidenceUpdate,
    request_id: str | None = None,
) -> dict:
    evidence = db.scalar(
        select(Evidence)
        .join(CandidateProfile, CandidateProfile.id == Evidence.candidate_profile_id)
        .options(selectinload(Evidence.source))
        .where(
            Evidence.id == evidence_id,
            CandidateProfile.user_id == user_id,
        )
        .with_for_update()
    )
    if evidence is None:
        raise DomainError("NOT_FOUND", "Evidence not found", 404)
    if evidence.revision != payload.expected_revision:
        raise DomainError(
            "REVISION_CONFLICT",
            "Evidence has changed.",
            409,
            {"current_revision": evidence.revision},
        )
    if evidence.status not in {"DRAFT", "PENDING_REVIEW"}:
        raise DomainError(
            "EVIDENCE_NOT_EDITABLE",
            "Only draft or pending-review evidence can be corrected. Approved, rejected, and archived evidence is immutable.",
            409,
        )

    approved_claim_id = db.scalar(
        select(CandidateClaim.id)
        .join(
            CandidateClaimEvidence,
            CandidateClaimEvidence.candidate_claim_id == CandidateClaim.id,
        )
        .where(
            CandidateClaimEvidence.evidence_id == evidence.id,
            CandidateClaim.status == "APPROVED",
        )
        .limit(1)
    )
    if approved_claim_id is not None:
        raise DomainError(
            "EVIDENCE_SUPPORTS_APPROVED_CLAIM",
            "Evidence supporting an approved claim cannot be edited.",
            409,
        )

    package_items = db.scalars(
        select(ApplicationPackageItem)
        .join(ApplicationPackage, ApplicationPackage.id == ApplicationPackageItem.package_id)
        .join(Application, Application.id == ApplicationPackage.application_id)
        .where(Application.candidate_profile_id == evidence.candidate_profile_id)
    ).all()
    if any(_contains_reference(item.evidence_refs or [], str(evidence.id)) for item in package_items):
        raise DomainError(
            "EVIDENCE_REFERENCED_BY_PACKAGE",
            "Evidence referenced by an application package cannot be edited; create a new evidence record instead.",
            409,
        )

    supplied = payload.model_fields_set.intersection({
        "evidence_type", "content", "effective_from", "effective_to", "expires_at"
    })
    changed_fields = []
    for field in sorted(supplied):
        new_value = getattr(payload, field)
        if getattr(evidence, field) != new_value:
            changed_fields.append(field)
    if not changed_fields:
        raise DomainError("NO_CHANGES", "The correction does not change any evidence fields.", 409)

    now = datetime.now(timezone.utc)
    invalidated_claims = db.scalars(
        select(CandidateClaim)
        .join(
            CandidateClaimEvidence,
            CandidateClaimEvidence.candidate_claim_id == CandidateClaim.id,
        )
        .where(
            CandidateClaimEvidence.evidence_id == evidence.id,
            CandidateClaim.status == "PENDING_REVIEW",
        )
        .with_for_update()
    ).all()
    previous_revision = evidence.revision
    previous_status = evidence.status
    for field in changed_fields:
        setattr(evidence, field, getattr(payload, field))
    evidence.revision += 1
    evidence.updated_at = now

    invalidated_claim_ids = []
    for claim in invalidated_claims:
        claim.status = "DRAFT"
        claim.revision += 1
        claim.updated_at = now
        invalidated_claim_ids.append(str(claim.id))
        db.add(AuditEvent(
            event_type="CLAIM_REVIEW_INVALIDATED_BY_EVIDENCE_UPDATE",
            resource_type="candidate_claim",
            resource_id=claim.id,
            actor_type="USER",
            actor_id=user_id,
            from_status="PENDING_REVIEW",
            to_status="DRAFT",
            reason=payload.reason,
            details={
                "evidence_id": str(evidence.id),
                "previous_revision": claim.revision - 1,
                "revision": claim.revision,
            },
            request_id=request_id,
            occurred_at=now,
        ))

    source = evidence.source
    db.add(AuditEvent(
        event_type="EVIDENCE_UPDATED",
        resource_type="evidence",
        resource_id=evidence.id,
        actor_type="USER",
        actor_id=user_id,
        from_status=previous_status,
        to_status=evidence.status,
        reason=payload.reason,
        details={
            "changed_fields": changed_fields,
            "previous_revision": previous_revision,
            "revision": evidence.revision,
            "source_id": str(source.id),
            "source_type": source.source_type,
            "source_reference": source.source_reference,
            "invalidated_claim_ids": invalidated_claim_ids,
        },
        request_id=request_id,
        occurred_at=now,
    ))
    db.commit()
    return get_evidence_library_item(db, user_id, evidence.id)
