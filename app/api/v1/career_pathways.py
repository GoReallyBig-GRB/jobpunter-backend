"""Candidate-owned career pathway creation, suggestions, review, and history."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.career_pathways.schemas import (
    CareerPathwayCreate, CareerPathwayDecision, CareerPathwayEventResponse,
    CareerPathwayResponse, CareerPathwaySuggestionRequest, CareerPathwayUpdate,
)
from app.application.career_pathways.service import CareerPathwayService
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate/career-pathways", tags=["candidate career pathways"])


def _response(pathway):
    return CareerPathwayResponse.model_validate(pathway, from_attributes=True)


@router.get("", response_model=dict)
def list_pathways(
    approval_status: str | None = Query(default=None, pattern="^(DRAFT|PENDING_REVIEW|APPROVED|REJECTED|RETIRED)$"),
    source_type: str | None = Query(default=None, pattern="^(USER_DEFINED|AI_SUGGESTED)$"),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    rows = CareerPathwayService(db).list(
        principal.user_id, approval_status=approval_status, source_type=source_type
    )
    return {"data": [_response(row) for row in rows]}


@router.post("", response_model=dict, status_code=201)
def create_pathway(
    payload: CareerPathwayCreate,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    row = CareerPathwayService(db).create_user_defined(principal.user_id, payload)
    return {"data": _response(row)}


@router.post("/suggestions", response_model=dict, status_code=201)
async def suggest_pathways(
    payload: CareerPathwaySuggestionRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    rows = await CareerPathwayService(db).suggest(
        principal.user_id, instruction=payload.instruction,
        request_id=request.headers.get("x-request-id"),
    )
    return {"data": [_response(row) for row in rows]}


@router.get("/{pathway_id}", response_model=dict)
def get_pathway(
    pathway_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    return {"data": _response(CareerPathwayService(db).get(principal.user_id, pathway_id))}


@router.patch("/{pathway_id}", response_model=dict)
def update_pathway(
    pathway_id: UUID,
    payload: CareerPathwayUpdate,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    return {"data": _response(CareerPathwayService(db).update(principal.user_id, pathway_id, payload))}


@router.post("/{pathway_id}/approve", response_model=dict)
def approve_pathway(
    pathway_id: UUID, payload: CareerPathwayDecision,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    row = CareerPathwayService(db).transition(
        principal.user_id, pathway_id, "approve", payload.expected_revision, payload.reason
    )
    return {"data": _response(row)}


@router.post("/{pathway_id}/reject", response_model=dict)
def reject_pathway(
    pathway_id: UUID, payload: CareerPathwayDecision,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    row = CareerPathwayService(db).transition(
        principal.user_id, pathway_id, "reject", payload.expected_revision, payload.reason
    )
    return {"data": _response(row)}


@router.post("/{pathway_id}/retire", response_model=dict)
def retire_pathway(
    pathway_id: UUID, payload: CareerPathwayDecision,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    row = CareerPathwayService(db).transition(
        principal.user_id, pathway_id, "retire", payload.expected_revision, payload.reason
    )
    return {"data": _response(row)}


@router.get("/{pathway_id}/events", response_model=dict)
def list_pathway_events(
    pathway_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    rows = CareerPathwayService(db).events(principal.user_id, pathway_id)
    return {"data": [CareerPathwayEventResponse.model_validate(row, from_attributes=True) for row in rows]}
