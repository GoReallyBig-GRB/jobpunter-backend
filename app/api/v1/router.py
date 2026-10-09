from fastapi import APIRouter
from .candidates import router as candidate_router
from .opportunities import router as opportunities_router

router = APIRouter(prefix="/api/v1")

@router.get("/health")
def health() -> dict:
    return {"data": {"status": "ok"}}

router.include_router(candidate_router)
router.include_router(opportunities_router)
