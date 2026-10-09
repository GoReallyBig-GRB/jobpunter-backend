from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.opportunities.evaluation_schemas import CandidateEvaluationCreate, CandidateEvaluationDetail\nfrom app.application.opportunities.evaluation_service import CandidateEvaluationService\nfrom app.application.opportunities.schemas import (
    ApplicationCreate, ApplicationResponse, ApplicationTransitionRequest,
    EvaluationResponse, PuntCreate, PuntResponse,
)
from app.application.opportunities.service import OpportunityService
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate", tags=["candidate opportunities"])


@router.post("/evaluations", response_model=dict, status_code=201)\ndef evaluate_job(\n    payload: CandidateEvaluationCreate,\n    principal: AuthenticatedPrincipal = Depends(get_principal),\n    db: Session = Depends(get_db),\n):\n    evaluation = CandidateEvaluationService(db).evaluate(\n        principal.user_id, payload.job_id, payload.target_role_key\n    )\n    return {"data": CandidateEvaluationDetail.model_validate(evaluation, from_attributes=True)}\n\n\n@router.get("/evaluations", response_model=dict)\ndef list_evaluations(
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    rows = OpportunityService(db).list_evaluations(principal.user_id)
    return {"data": [EvaluationResponse.model_validate(row, from_attributes=True) for row in rows]}


@router.post("/punts", response_model=dict, status_code=201)
def create_punt(
    payload: PuntCreate,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    punt = OpportunityService(db).create_punt(principal.user_id, payload.evaluation_id)
    return {"data": PuntResponse.model_validate(punt, from_attributes=True)}


@router.get("/punts", response_model=dict)
def list_punts(
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    rows = OpportunityService(db).list_punts(principal.user_id)
    return {"data": [PuntResponse.model_validate(row, from_attributes=True) for row in rows]}


@router.post("/applications", response_model=dict, status_code=201)
def create_application(
    payload: ApplicationCreate,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    application = OpportunityService(db).create_application(principal.user_id, payload.punt_id)
    return {"data": ApplicationResponse.model_validate(application, from_attributes=True)}


@router.get("/applications", response_model=dict)
def list_applications(
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    rows = OpportunityService(db).list_applications(principal.user_id)
    return {"data": [ApplicationResponse.model_validate(row, from_attributes=True) for row in rows]}


@router.post("/applications/{application_id}/transition", response_model=dict)
def transition_application(
    application_id: UUID,
    payload: ApplicationTransitionRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    application = OpportunityService(db).transition_application(
        principal.user_id, application_id, payload.target_status, payload.expected_revision
    )
    return {"data": ApplicationResponse.model_validate(application, from_attributes=True)}
