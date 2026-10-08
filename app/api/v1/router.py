from fastapi import APIRouter
from .candidates import router as candidate_router

router = APIRouter(prefix="/api/v1")

@router.get("/health")
def health() -> dict:
    return {"data": {"status": "ok"}}

router.include_router(candidate_router)
