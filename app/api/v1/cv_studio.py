"""Candidate-scoped CV Studio API; every write creates an immutable version."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.cv_studio.schemas import (
    CVVariantCreateRequest, CVVersionApprovalRequest, CVVersionSaveRequest,
    MasterCVRegenerateRequest,
)
from app.application.cv_studio.service import CVStudioService
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate/cvs", tags=["CV Studio"])


@router.get("", response_model=dict)
def list_cvs(
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    return {"data": CVStudioService(db).list_cvs(principal.user_id)}


@router.post("/master/regenerate", response_model=dict, status_code=201)
def regenerate_master_cv(
    payload: MasterCVRegenerateRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    version = CVStudioService(db).regenerate_master(
        principal.user_id, payload.expected_profile_revision
    )
    return {"data": CVStudioService._serialize(version, None)}


@router.post("/variants", response_model=dict, status_code=201)
def create_cv_variant(
    payload: CVVariantCreateRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    version = CVStudioService(db).create_variant(
        principal.user_id, payload.cv_key, payload.title,
        payload.source_master_version_id, payload.content,
    )
    return {"data": CVStudioService._serialize(version, None)}


@router.get("/{cv_key}", response_model=dict)
def get_cv(
    cv_key: str,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    return {"data": CVStudioService(db).get_cv(principal.user_id, cv_key)}


@router.post("/{cv_key}/versions", response_model=dict, status_code=201)
def save_cv_version(
    cv_key: str,
    payload: CVVersionSaveRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    version = CVStudioService(db).save_version(
        principal.user_id, cv_key, payload.expected_latest_version,
        payload.content, payload.title,
    )
    return {"data": CVStudioService._serialize(version, None)}


@router.post("/{cv_key}/versions/{version_number}/approve", response_model=dict)
def approve_cv_version(
    cv_key: str,
    version_number: int,
    payload: CVVersionApprovalRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    approval = CVStudioService(db).approve_version(
        principal.user_id, cv_key, version_number, payload.notes
    )
    return {"data": {
        "id": str(approval.id),
        "cv_version_id": str(approval.cv_version_id),
        "approved_by_user_id": str(approval.approved_by_user_id),
        "approved_at": approval.approved_at.isoformat(),
        "notes": approval.notes,
    }}
