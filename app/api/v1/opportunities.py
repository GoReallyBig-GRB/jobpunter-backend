from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.opportunities.evaluation_schemas import CandidateEvaluationCreate, CandidateEvaluationDetail
from app.application.opportunities.evaluation_service import CandidateEvaluationService
from app.application.opportunities.schemas import (
    ApplicationCreate, ApplicationResponse, ApplicationTransitionRequest,
    EvaluationResponse, PuntCreate, PuntResponse,
)
from app.application.opportunities.service import OpportunityService
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate", tags=["candidate opportunities"])


class EvaluationOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_disposition: str = Field(alias="newDisposition", pattern="^(APPLY|REVIEW|WATCH|REJECT)$")
    expected_revision: int = Field(alias="expectedRevision", ge=0)
    reason: str = Field(min_length=3, max_length=1000)
    notes: str | None = Field(default=None, max_length=5000)


def _override_payload(row):
    return {
        "id": str(row.id),
        "evaluation_id": str(row.evaluation_id),
        "revision": row.revision,
        "original_decision": row.original_decision,
        "new_disposition": row.new_disposition,
        "actor_user_id": str(row.actor_user_id),
        "reason": row.reason,
        "notes": row.notes,
        "created_at": row.created_at.isoformat(),
    }


@router.post("/evaluations", response_model=dict, status_code=201)
async def evaluate_job(
    payload: CandidateEvaluationCreate,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    evaluation = await CandidateEvaluationService(db).evaluate(
        principal.user_id, payload.job_id, payload.target_role_key
    )
    return {"data": CandidateEvaluationDetail.model_validate(evaluation, from_attributes=True)}


@router.get("/evaluations", response_model=dict)
def list_evaluations(
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    rows = OpportunityService(db).list_evaluations(principal.user_id)
    return {"data": [EvaluationResponse.model_validate(row, from_attributes=True) for row in rows]}


@router.post("/evaluations/{evaluation_id}/overrides", response_model=dict, status_code=201)
def override_evaluation(
    evaluation_id: UUID,
    payload: EvaluationOverrideRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    row = OpportunityService(db).override_evaluation(
        principal.user_id, evaluation_id, payload.new_disposition,
        payload.expected_revision, payload.reason, payload.notes,
    )
    return {"data": _override_payload(row)}


@router.get("/evaluations/{evaluation_id}/overrides", response_model=dict)
def list_evaluation_overrides(
    evaluation_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    rows = OpportunityService(db).list_evaluation_overrides(principal.user_id, evaluation_id)
    return {"data": [_override_payload(row) for row in rows]}


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
