from fastapi import APIRouter
from .admin import router as admin_router
from .application_packages import router as application_packages_router
from .document_imports import router as document_imports_router
from .cv_studio import router as cv_studio_router
from .discovery import router as discovery_router
from .ai import router as ai_router
from .email_parser import router as email_parser_router
from .manual_ingestion import router as manual_ingestion_router
from .candidates import router as candidate_router
from .opportunities import router as opportunities_router
from .preferences import router as preferences_router
from .career_pathways import router as career_pathways_router
from .requirement_review import router as requirement_review_router

router = APIRouter(prefix="/api/v1")

@router.get("/health")
def health() -> dict:
    return {"data": {"status": "ok"}}

router.include_router(candidate_router)
router.include_router(opportunities_router)
router.include_router(application_packages_router)
router.include_router(document_imports_router)
router.include_router(cv_studio_router)
router.include_router(discovery_router)
router.include_router(preferences_router)
router.include_router(career_pathways_router)
router.include_router(requirement_review_router)
router.include_router(ai_router)
router.include_router(email_parser_router)
router.include_router(manual_ingestion_router)
router.include_router(admin_router)
