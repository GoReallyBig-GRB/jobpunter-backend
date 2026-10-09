from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.ai.schemas import GenerateRequest, GenerateResponse
from app.application.ai.service import execute_ai_task
from app.ports.identity import AuthenticatedPrincipal
from app.infrastructure.db.session import get_db

router = APIRouter(tags=["ai"])

MAX_REQUEST_BYTES = 100_000


@router.post("/ai/generate", response_model=GenerateResponse)
async def generate_ai(
    payload: GenerateRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> GenerateResponse:
    # Pydantic validates task IDs, prompt bounds, options, and rejects unknown fields.
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={"code": "REQUEST_TOO_LARGE", "message": "AI request is too large."},
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header.") from exc
    return await execute_ai_task(payload, db=db, user_id=principal.user_id, request_id=request.headers.get("x-request-id"))
