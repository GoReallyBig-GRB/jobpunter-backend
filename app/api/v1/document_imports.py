"""Candidate-scoped upload, parsing, and preview for source documents."""
from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session
from uuid import UUID

from app.api.deps import get_principal
from app.application.documents.imports import DocumentImportService, MAX_DOCUMENT_BYTES
from app.infrastructure.db.models import Document
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate/document-imports", tags=["document imports"])


def _response(record, document, *, include_preview: bool = False) -> dict:
    data = {
        "id": str(record.id),
        "document_id": str(record.document_id),
        "status": record.status,
        "filename": document.filename,
        "mime_type": document.mime_type,
        "byte_size": document.byte_size,
        "revision": record.revision,
        "conflicts": record.conflicts,
        "error_code": record.error_code,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }
    if include_preview:
        data.update({
            "extracted_text": record.extracted_text,
            "extracted_fields": record.extracted_fields,
        })
    return {"data": data}


@router.post("", response_model=dict, status_code=201)
async def upload_document_import(
    file: UploadFile = File(...),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    content = await file.read(MAX_DOCUMENT_BYTES + 1)
    try:
        record = DocumentImportService(db).upload(
            principal.user_id,
            file.filename or "",
            file.content_type or "",
            content,
        )
    finally:
        await file.close()
    document = db.get(Document, record.document_id)
    return _response(record, document)


@router.post("/{import_id}/parse", response_model=dict)
def parse_document_import(
    import_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    record = DocumentImportService(db).parse(principal.user_id, import_id)
    document = db.get(Document, record.document_id)
    return _response(record, document, include_preview=True)


@router.get("/{import_id}/preview", response_model=dict)
def preview_document_import(
    import_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    record = DocumentImportService(db).preview(principal.user_id, import_id)
    document = db.get(Document, record.document_id)
    return _response(record, document, include_preview=True)
