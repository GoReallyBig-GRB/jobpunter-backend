from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.preferences.schemas import (
    CandidatePreferencesPut,
    CandidatePreferencesResponse,
)
from app.application.preferences.service import CandidatePreferencesService
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate/preferences", tags=["candidate preferences"])


@router.get("", response_model=CandidatePreferencesResponse)
def get_candidate_preferences(
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> CandidatePreferencesResponse:
    revision, preferences = CandidatePreferencesService(db).get(principal.user_id)
    return CandidatePreferencesResponse(revision=revision, preferences=preferences)


@router.put("", response_model=CandidatePreferencesResponse)
def put_candidate_preferences(
    payload: CandidatePreferencesPut,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> CandidatePreferencesResponse:
    revision, preferences = CandidatePreferencesService(db).put(
        principal.user_id,
        payload.expected_revision,
        payload.preferences,
    )
    return CandidatePreferencesResponse(revision=revision, preferences=preferences)
