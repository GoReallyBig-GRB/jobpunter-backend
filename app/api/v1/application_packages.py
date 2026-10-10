from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.application_packages.schemas import (
    ApplicationPackageCreate, ApplicationPackageItemResponse, ApplicationPackageResponse,
)
from app.application.application_packages.service import ApplicationPackageService
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate/applications/{application_id}/packages", tags=["application packages"])


def _response(package, items):
    return ApplicationPackageResponse(
        id=package.id,
        application_id=package.application_id,
        version_number=package.version_number,
        status=package.status,
        source_snapshot=package.source_snapshot,
        created_by_user_id=package.created_by_user_id,
        created_at=package.created_at,
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
    items = list(db.query(__import__("app.infrastructure.db.models", fromlist=["ApplicationPackageItem"]).ApplicationPackageItem)
                 .filter_by(package_id=package.id).all())
    return {"data": _response(package, items)}


@router.get("", response_model=dict)
def list_application_packages(
    application_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    rows = ApplicationPackageService(db).list_for_application(principal.user_id, application_id)
    return {"data": [_response(package, items) for package, items in rows]}


@router.get("/{package_id}", response_model=dict)
def get_application_package(
    application_id: UUID,
    package_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    package, items = ApplicationPackageService(db).get(principal.user_id, application_id, package_id)
    return {"data": _response(package, items)}
