from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import get_principal
from app.application.candidates.schemas import (
    CandidateProfileResponse, EvidenceCreate, EvidenceResponse, EvidenceTransitionRequest,
    CandidateClaimCreate, CandidateClaimResponse, CandidateClaimTransitionRequest,
)
from app.application.candidates.service import CandidateService
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate", tags=["candidate"])

@router.get("/profile", response_model=dict)
def get_profile(principal: AuthenticatedPrincipal = Depends(get_principal), db: Session = Depends(get_db)):
    profile = CandidateService(db).get_or_create_profile(principal.user_id)
    return {"data": CandidateProfileResponse(id=profile.id, user_id=profile.user_id, status=profile.status, revision=profile.revision)}

@router.post("/evidence", response_model=dict, status_code=201)
def create_evidence(payload: EvidenceCreate, principal: AuthenticatedPrincipal = Depends(get_principal), db: Session = Depends(get_db)):
    evidence = CandidateService(db).create_evidence(principal.user_id, payload)
    return {"data": EvidenceResponse.model_validate(evidence, from_attributes=True)}

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
