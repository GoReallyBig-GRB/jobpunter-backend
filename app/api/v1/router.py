from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.infrastructure.db.schema_revision import CURRENT_SCHEMA_REVISION
from app.infrastructure.db.session import get_db

from .admin import router as admin_router
from .admin_discovery import router as admin_discovery_router
from .ai import router as ai_router
from .application_packages import router as application_packages_router
from .candidates import router as candidate_router
from .career_pathways import router as career_pathways_router
from .cv_studio import router as cv_studio_router
from .discovery import router as discovery_router
from .document_imports import router as document_imports_router
from .documents import router as documents_router
from .email_parser import router as email_parser_router
from .job_pool import router as job_pool_router
from .manual_ingestion import router as manual_ingestion_router
from .notifications import router as notifications_router
from .opportunities import router as opportunities_router
from .preferences import router as preferences_router
from .requirement_review import router as requirement_review_router
from .saved_jobs import router as saved_jobs_router
from .support import admin_router as admin_support_router
from .support import candidate_router as candidate_support_router

router = APIRouter(prefix="/api/v1")

@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {"data": {
        "status": "ok",
        "deployment_revision": settings.deployment_revision or "unknown",
    }}


def _code_migration_heads() -> tuple[str, ...]:
    return (CURRENT_SCHEMA_REVISION,)


@router.get("/readiness", response_model=dict)
def readiness(db: Session = Depends(get_db)):  # noqa: B008 - FastAPI dependency declaration
    """Prove database/schema readiness without exposing credentials or topology."""
    settings = get_settings()
    environment = settings.app_env.strip().lower()
    revision_configured = bool(settings.deployment_revision) or environment == "development"
    database_ready = False
    database_revisions: tuple[str, ...] = ()
    code_heads: tuple[str, ...] = ()
    try:
        db.execute(text("SELECT 1"))
        database_revisions = tuple(sorted(
            str(value) for value in db.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars().all()
        ))
        code_heads = _code_migration_heads()
        database_ready = True
    except Exception:  # noqa: BLE001 - public readiness must fail closed for any dependency error
        # Readiness is intentionally safe and bounded: do not expose driver,
        # host, credential, SQL, or filesystem error details.
        database_ready = False

    schema_current = bool(database_ready and code_heads and database_revisions == code_heads)
    ready = database_ready and schema_current and revision_configured
    payload = {
        "data": {
            "status": "ready" if ready else "not_ready",
            "environment": environment,
            "deployment_revision": settings.deployment_revision or "unknown",
            "checks": {
                "database": database_ready,
                "schema_current": schema_current,
                "deployment_revision_configured": revision_configured,
            },
            "schema": {
                "database_revisions": list(database_revisions),
                "code_heads": list(code_heads),
            },
        }
    }
    return JSONResponse(status_code=200 if ready else 503, content=payload)

router.include_router(candidate_router)
router.include_router(saved_jobs_router)
router.include_router(opportunities_router)
router.include_router(application_packages_router)
router.include_router(document_imports_router)
router.include_router(documents_router)
router.include_router(cv_studio_router)
router.include_router(discovery_router)
router.include_router(preferences_router)
router.include_router(career_pathways_router)
router.include_router(requirement_review_router)
router.include_router(notifications_router)
router.include_router(candidate_support_router)
router.include_router(admin_support_router)
router.include_router(ai_router)
router.include_router(email_parser_router)
router.include_router(manual_ingestion_router)
router.include_router(job_pool_router)
router.include_router(admin_router)
router.include_router(admin_discovery_router)
