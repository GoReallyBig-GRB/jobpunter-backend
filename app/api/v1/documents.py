"""Candidate-owned document recovery endpoint."""
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.documents.restore import DocumentRestoreService
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate/documents", tags=["documents"])


@router.post("/{document_id}/restore", response_model=dict)
def restore_document(
    document_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    return {"data": DocumentRestoreService(db).restore(principal.user_id, document_id)}
