"""Candidate-scoped upload, parsing, and preview for source documents."""
from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from uuid import UUID

from app.api.deps import get_principal
from app.application.documents.imports import DocumentImportService, MAX_DOCUMENT_BYTES
from app.infrastructure.db.models import Document
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate/document-imports", tags=["document imports"])


class DocumentImportConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    conflicts_reviewed: bool = False


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
        "preview_available": bool(record.extracted_text),
        "confirmed_at": record.confirmed_at.isoformat() if record.confirmed_at else None,
        "confirmed_by_user_id": str(record.confirmed_by_user_id) if record.confirmed_by_user_id else None,
        "saved_evidence_id": str(record.saved_evidence_id) if record.saved_evidence_id else None,
        "failure_category": record.error_code if record.status == "FAILED" else None,
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


@router.post("/{import_id}/extract-fields", response_model=dict)
async def extract_document_import_fields(
    import_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    record = await DocumentImportService(db).extract_fields(principal.user_id, import_id)
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

@router.post("/{import_id}/identify-conflicts", response_model=dict)
def identify_document_import_conflicts(
    import_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    record = DocumentImportService(db).identify_conflicts(principal.user_id, import_id)
    document = db.get(Document, record.document_id)
    return _response(record, document, include_preview=True)


@router.post("/{import_id}/confirm", response_model=dict)
def confirm_document_import(
    import_id: UUID,
    payload: DocumentImportConfirmRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    record = DocumentImportService(db).confirm(
        principal.user_id,
        import_id,
        expected_revision=payload.expected_revision,
        conflicts_reviewed=payload.conflicts_reviewed,
    )
    document = db.get(Document, record.document_id)
    return _response(record, document, include_preview=True)


@router.post("/{import_id}/save", response_model=dict)
def save_document_import(
    import_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    record, evidence = DocumentImportService(db).save(principal.user_id, import_id)
    document = db.get(Document, record.document_id)
    return {"data": {
        **_response(record, document, include_preview=True)["data"],
        "evidence": {
            "id": str(evidence.id),
            "status": evidence.status,
            "evidence_type": evidence.evidence_type,
            "evidence_source_id": str(evidence.evidence_source_id),
        },
    }}



@router.get("", response_model=dict)
def list_document_import_history(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    if limit < 1 or limit > 100 or offset < 0:
        from app.api.errors import DomainError
        raise DomainError("INVALID_PAGINATION", "Limit must be 1-100 and offset must be non-negative.", 422)
    if status is not None and status not in {
        "UPLOADED", "PARSED", "PREVIEWED", "CONFLICTS_IDENTIFIED",
        "CONFIRMED", "SAVED", "FAILED",
    }:
        from app.api.errors import DomainError
        raise DomainError("INVALID_DOCUMENT_IMPORT_STATUS", "Unsupported document import status.", 422)
    records, total = DocumentImportService(db).list_history(
        principal.user_id, limit=limit, offset=offset, status=status
    )
    return {
        "data": [_response(record, document)["data"] for record, document in records],
        "pagination": {"limit": limit, "offset": offset, "total": total},
    }


@router.get("/{import_id}", response_model=dict)
def get_document_import_history_item(
    import_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
):
    record, document = DocumentImportService(db).get_history_item(principal.user_id, import_id)
    return _response(record, document, include_preview=bool(record.extracted_text))
