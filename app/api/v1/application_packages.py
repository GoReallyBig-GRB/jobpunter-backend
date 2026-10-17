from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.application_packages.schemas import (
    ApplicationPackageCreate, ApplicationPackageItemResponse, ApplicationPackageResponse,
)
from app.application.application_packages.review import ApplicationPackageReviewService
from app.application.application_packages.claim_validation import PackageClaimValidationService
from app.application.application_packages.service import ApplicationPackageService
from app.application.documents.service import DocumentGenerationService
from app.infrastructure.db.models import ApplicationPackageItem
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate/applications/{application_id}/packages", tags=["application packages"])


class ApplicationPackageReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "request_revision"]


class DocumentGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_type: Literal["tailored_cv", "cover_letter", "application_q_and_a"]
    output_format: Literal["docx", "pdf"]


def _response(package, items):
    return ApplicationPackageResponse(
        id=package.id,
        application_id=package.application_id,
        version_number=package.version_number,
        status=package.status,
        source_snapshot=package.source_snapshot,
        created_by_user_id=package.created_by_user_id,
        created_at=package.created_at,
        review_decided_at=package.review_decided_at,
        review_decided_by_user_id=package.review_decided_by_user_id,
        items=[ApplicationPackageItemResponse.model_validate(item, from_attributes=True) for item in items],
    )


@router.post("", response_model=dict, status_code=201)
def create_application_package(
    application_id: UUID,
    payload: ApplicationPackageCreate,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    package = ApplicationPackageService(db).create(principal.user_id, application_id, payload)
    items = list(db.query(ApplicationPackageItem).filter_by(package_id=package.id).all())
    return {"data": _response(package, items)}


@router.get("", response_model=dict)
def list_application_packages(
    application_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    rows = ApplicationPackageService(db).list_for_application(principal.user_id, application_id)
    return {"data": [_response(package, items) for package, items in rows]}




@router.post("/{package_id}/revisions", response_model=dict, status_code=201)
def revise_application_package(
    application_id: UUID,
    package_id: UUID,
    payload: ApplicationPackageCreate,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    service = ApplicationPackageService(db)
    source_package, _source_items = service.get(principal.user_id, application_id, package_id)
    package = service.create(
        principal.user_id, application_id, payload,
        expected_current_version=source_package.version_number,
    )
    items = list(db.query(ApplicationPackageItem).filter_by(package_id=package.id).all())
    return {"data": _response(package, items)}


@router.get("/{package_id}", response_model=dict)
def get_application_package(
    application_id: UUID,
    package_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    package, items = ApplicationPackageService(db).get(principal.user_id, application_id, package_id)
    return {"data": _response(package, items)}


@router.post("/{package_id}/validate-claims", response_model=dict)
async def validate_application_package_claims(
    application_id: UUID,
    package_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    result = await PackageClaimValidationService(db).validate(
        principal.user_id, application_id, package_id
    )
    return {"data": result}


@router.get("/{package_id}/claim-validation", response_model=dict)
def get_application_package_claim_validation(
    application_id: UUID,
    package_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    result = PackageClaimValidationService(db).get_latest(
        principal.user_id, application_id, package_id
    )
    return {"data": result}


@router.post("/{package_id}/prepare-review", response_model=dict)
def prepare_application_package_review(
    application_id: UUID,
    package_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    result = ApplicationPackageReviewService(db).prepare(
        principal.user_id, application_id, package_id
    )
    package = result["package"]
    items = list(db.query(ApplicationPackageItem).filter_by(package_id=package.id).all())
    return {"data": {
        "package": _response(package, items),
        "ready": result["ready"],
        "blockers": result["blockers"],
    }}


@router.post("/{package_id}/review-decision", response_model=dict)
def decide_application_package_review(
    application_id: UUID,
    package_id: UUID,
    payload: ApplicationPackageReviewDecision,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    package = ApplicationPackageReviewService(db).decide(
        principal.user_id, application_id, package_id, payload.decision
    )
    items = list(db.query(ApplicationPackageItem).filter_by(package_id=package.id).all())
    return {"data": _response(package, items)}

@router.post("/{package_id}/documents", response_model=dict, status_code=201)
def generate_package_document(
    application_id: UUID,
    package_id: UUID,
    payload: DocumentGenerateRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    document = DocumentGenerationService(db).generate(
        principal.user_id,
        application_id,
        package_id,
        payload.artifact_type,
        payload.output_format,
    )
    return {"data": {
        "id": str(document.id),
        "application_package_id": str(document.application_package_id),
        "application_package_item_id": str(document.application_package_item_id),
        "document_type": document.document_type,
        "filename": document.filename,
        "mime_type": document.mime_type,
        "checksum_sha256": document.checksum_sha256,
        "byte_size": document.byte_size,
        "created_at": document.created_at.isoformat(),
        "metadata": document.metadata_json,
    }}


@router.get("/{package_id}/documents/{document_id}/download", response_model=dict)
def create_package_document_download_url(
    application_id: UUID,
    package_id: UUID,
    document_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    result = DocumentGenerationService(db).create_download_url(
        principal.user_id, application_id, package_id, document_id
    )
    return {"data": result}

