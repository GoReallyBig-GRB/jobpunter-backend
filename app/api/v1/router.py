from fastapi import APIRouter
from .admin import router as admin_router
from .application_packages import router as application_packages_router
from .ai import router as ai_router
from .email_parser import router as email_parser_router
from .candidates import router as candidate_router
from .opportunities import router as opportunities_router
from .preferences import router as preferences_router
from .requirement_review import router as requirement_review_router

router = APIRouter(prefix="/api/v1")

@router.get("/health")
def health() -> dict:
    return {"data": {"status": "ok"}}

router.include_router(candidate_router)
router.include_router(opportunities_router)
router.include_router(application_packages_router)
router.include_router(preferences_router)
router.include_router(requirement_review_router)
router.include_router(ai_router)
router.include_router(email_parser_router)
router.include_router(admin_router)
