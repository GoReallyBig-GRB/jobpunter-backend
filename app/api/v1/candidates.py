from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.api.deps import get_principal
from app.application.candidates.schemas import (
    CandidateProfileResponse, CandidateProfileSections, CandidateProfileUpdate,
    EvidenceCreate, EvidenceResponse, EvidenceTransitionRequest,
    EvidenceLibraryItemResponse, EvidenceLibraryListResponse,
    CandidateClaimCreate, CandidateClaimResponse, CandidateClaimTransitionRequest, CandidateClaimUpdate,
)
from app.application.candidates.service import CandidateService
from app.application.candidates.evidence_library import list_evidence_library, get_evidence_library_item
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate", tags=["candidate"])

@router.get("/profile", response_model=dict)
def get_profile(principal: AuthenticatedPrincipal = Depends(get_principal), db: Session = Depends(get_db)):
    profile = CandidateService(db).get_or_create_profile(principal.user_id)
    return {"data": CandidateProfileResponse(
        id=profile.id,
        user_id=profile.user_id,
        status=profile.status,
        revision=profile.revision,
        profile_data=CandidateProfileSections.model_validate(profile.profile_data or {}),
    )}


@router.patch("/profile", response_model=dict)
def update_profile(
    payload: CandidateProfileUpdate,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    profile = CandidateService(db).update_profile(
        principal.user_id,
        payload.expected_revision,
        payload.sections.model_dump(exclude_unset=True),
        fact_sources={
            path: source.model_dump()
            for path, source in payload.fact_sources.items()
        },
    )
    return {"data": CandidateProfileResponse(
        id=profile.id,
        user_id=profile.user_id,
        status=profile.status,
        revision=profile.revision,
        profile_data=CandidateProfileSections.model_validate(profile.profile_data or {}),
    )}

@router.get("/profile/provenance", response_model=dict)
def get_profile_provenance(
    section: str | None = None,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    records = CandidateService(db).list_profile_fact_provenance(principal.user_id, section)
    return {"data": [
        {
            "id": str(record.id),
            "candidate_profile_id": str(record.candidate_profile_id),
            "section": record.section,
            "fact_path": record.fact_path,
            "fact_value": record.fact_value,
            "profile_revision": record.profile_revision,
            "source_type": record.source_type,
            "source_reference": record.source_reference,
            "evidence_id": str(record.evidence_id) if record.evidence_id else None,
            "document_import_id": str(record.document_import_id) if record.document_import_id else None,
            "source_excerpt": record.source_excerpt,
            "confidence": record.confidence,
            "confirmed_at": record.confirmed_at.isoformat() if record.confirmed_at else None,
            "confirmed_by_user_id": str(record.confirmed_by_user_id) if record.confirmed_by_user_id else None,
            "created_at": record.created_at.isoformat(),
        }
        for record in records
    ]}


@router.post("/evidence", response_model=dict, status_code=201)
def create_evidence(payload: EvidenceCreate, principal: AuthenticatedPrincipal = Depends(get_principal), db: Session = Depends(get_db)):
    evidence = CandidateService(db).create_evidence(principal.user_id, payload)
    return {"data": EvidenceResponse.model_validate(evidence, from_attributes=True)}

@router.get("/evidence", response_model=EvidenceLibraryListResponse)
def list_evidence(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None, min_length=1, max_length=32),
    evidence_type: str | None = Query(default=None, min_length=1, max_length=64),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    items, total = list_evidence_library(
        db,
        principal.user_id,
        limit=limit,
        offset=offset,
        status=status.strip() if status else None,
        evidence_type=evidence_type.strip() if evidence_type else None,
    )
    return {
        "data": [EvidenceLibraryItemResponse.model_validate(item) for item in items],
        "pagination": {"limit": limit, "offset": offset, "total": total},
    }


@router.get("/evidence/{evidence_id}", response_model=EvidenceLibraryItemResponse)
def get_evidence(
    evidence_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    item = get_evidence_library_item(db, principal.user_id, evidence_id)
    return EvidenceLibraryItemResponse.model_validate(item)


@router.post("/evidence/{evidence_id}/submit", response_model=dict)
def submit_evidence(evidence_id: UUID, payload: EvidenceTransitionRequest, principal: AuthenticatedPrincipal = Depends(get_principal), db: Session = Depends(get_db)):
    evidence = CandidateService(db).transition_evidence(principal.user_id, evidence_id, "PENDING_REVIEW", payload.expected_revision)
    return {"data": EvidenceResponse.model_validate(evidence, from_attributes=True)}

@router.post("/evidence/{evidence_id}/approve", response_model=dict)
def approve_evidence(evidence_id: UUID, payload: EvidenceTransitionRequest, principal: AuthenticatedPrincipal = Depends(get_principal), db: Session = Depends(get_db)):
    evidence = CandidateService(db).transition_evidence(principal.user_id, evidence_id, "APPROVED", payload.expected_revision)
    return {"data": EvidenceResponse.model_validate(evidence, from_attributes=True)}

@router.post("/evidence/{evidence_id}/reject", response_model=dict)
def reject_evidence(evidence_id: UUID, payload: EvidenceTransitionRequest, principal: AuthenticatedPrincipal = Depends(get_principal), db: Session = Depends(get_db)):
    evidence = CandidateService(db).transition_evidence(principal.user_id, evidence_id, "REJECTED", payload.expected_revision)
    return {"data": EvidenceResponse.model_validate(evidence, from_attributes=True)}

@router.post("/evidence/{evidence_id}/archive", response_model=dict)
def archive_evidence(evidence_id: UUID, payload: EvidenceTransitionRequest, principal: AuthenticatedPrincipal = Depends(get_principal), db: Session = Depends(get_db)):
    evidence = CandidateService(db).transition_evidence(principal.user_id, evidence_id, "ARCHIVED", payload.expected_revision)
    return {"data": EvidenceResponse.model_validate(evidence, from_attributes=True)}



@router.get("/claims", response_model=dict)
def list_claims(principal: AuthenticatedPrincipal = Depends(get_principal), db: Session = Depends(get_db)):
    service = CandidateService(db)
    claims = service.list_claims(principal.user_id)
    return {"data": [
        CandidateClaimResponse(
            id=claim.id,
            candidate_profile_id=claim.candidate_profile_id,
            claim_type=claim.claim_type,
            claim_content=claim.claim_content,
            status=claim.status,
            revision=claim.revision,
            evidence_ids=service.claim_evidence_ids(claim.id),
        )
        for claim in claims
    ]}


@router.post("/claims", response_model=dict, status_code=201)
def create_claim(payload: CandidateClaimCreate, principal: AuthenticatedPrincipal = Depends(get_principal), db: Session = Depends(get_db)):
    service = CandidateService(db)
    claim = service.create_claim(principal.user_id, payload)
    return {"data": CandidateClaimResponse(
        id=claim.id,
        candidate_profile_id=claim.candidate_profile_id,
        claim_type=claim.claim_type,
        claim_content=claim.claim_content,
        status=claim.status,
        revision=claim.revision,
        evidence_ids=service.claim_evidence_ids(claim.id),
    )}


@router.patch("/claims/{claim_id}", response_model=dict)
def update_claim(
    claim_id: UUID,
    payload: CandidateClaimUpdate,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    service = CandidateService(db)
    claim = service.update_claim(principal.user_id, claim_id, payload)
    return {"data": CandidateClaimResponse(
        id=claim.id,
        candidate_profile_id=claim.candidate_profile_id,
        claim_type=claim.claim_type,
        claim_content=claim.claim_content,
        status=claim.status,
        revision=claim.revision,
        evidence_ids=service.claim_evidence_ids(claim.id),
    )}


@router.post("/claims/{claim_id}/submit", response_model=dict)
def submit_claim(claim_id: UUID, payload: CandidateClaimTransitionRequest, principal: AuthenticatedPrincipal = Depends(get_principal), db: Session = Depends(get_db)):
    service = CandidateService(db)
    claim = service.transition_claim(principal.user_id, claim_id, "PENDING_REVIEW", payload.expected_revision)
    return {"data": CandidateClaimResponse(
        id=claim.id,
        candidate_profile_id=claim.candidate_profile_id,
        claim_type=claim.claim_type,
        claim_content=claim.claim_content,
        status=claim.status,
        revision=claim.revision,
        evidence_ids=service.claim_evidence_ids(claim.id),
    )}


@router.post("/claims/{claim_id}/approve", response_model=dict)
def approve_claim(claim_id: UUID, payload: CandidateClaimTransitionRequest, principal: AuthenticatedPrincipal = Depends(get_principal), db: Session = Depends(get_db)):
    service = CandidateService(db)
    claim = service.transition_claim(principal.user_id, claim_id, "APPROVED", payload.expected_revision)
    return {"data": CandidateClaimResponse(
        id=claim.id,
        candidate_profile_id=claim.candidate_profile_id,
        claim_type=claim.claim_type,
        claim_content=claim.claim_content,
        status=claim.status,
        revision=claim.revision,
        evidence_ids=service.claim_evidence_ids(claim.id),
    )}


@router.post("/claims/{claim_id}/reject", response_model=dict)
def reject_claim(claim_id: UUID, payload: CandidateClaimTransitionRequest, principal: AuthenticatedPrincipal = Depends(get_principal), db: Session = Depends(get_db)):
    service = CandidateService(db)
    claim = service.transition_claim(principal.user_id, claim_id, "REJECTED", payload.expected_revision)
    return {"data": CandidateClaimResponse(
        id=claim.id,
        candidate_profile_id=claim.candidate_profile_id,
        claim_type=claim.claim_type,
        claim_content=claim.claim_content,
        status=claim.status,
        revision=claim.revision,
        evidence_ids=service.claim_evidence_ids(claim.id),
    )}


@router.post("/claims/{claim_id}/archive", response_model=dict)
def archive_claim(claim_id: UUID, payload: CandidateClaimTransitionRequest, principal: AuthenticatedPrincipal = Depends(get_principal), db: Session = Depends(get_db)):
    service = CandidateService(db)
    claim = service.transition_claim(principal.user_id, claim_id, "ARCHIVED", payload.expected_revision)
    return {"data": CandidateClaimResponse(
        id=claim.id,
        candidate_profile_id=claim.candidate_profile_id,
        claim_type=claim.claim_type,
        claim_content=claim.claim_content,
        status=claim.status,
        revision=claim.revision,
        evidence_ids=service.claim_evidence_ids(claim.id),
    )}
