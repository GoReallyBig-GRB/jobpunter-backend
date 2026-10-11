"""Dependency-aware cleanup for terminal, unreferenced uploaded documents.

Run from a scheduled backend job. Candidate evidence and application-package artifacts are
intentionally excluded. Personal Drive backup copies are not deleted by this worker.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.infrastructure.db.models import Document, DocumentImport
from app.infrastructure.storage.factory import build_object_storage

ELIGIBLE_IMPORT_STATES = {"UPLOADED", "PARSED", "PREVIEWED", "CONFLICTS_IDENTIFIED", "CONFIRMED", "FAILED", "CANCELLED"}


def is_retention_eligible(document: Document, import_record: DocumentImport | None, cutoff: datetime) -> bool:
    if document.application_package_id is not None or document.application_package_item_id is not None:
        return False
    metadata = document.metadata_json or {}
    if metadata.get("retention", {}).get("status") == "EXPIRED":
        return False
    if import_record is None or import_record.saved_evidence_id is not None:
        return False
    if import_record.status not in ELIGIBLE_IMPORT_STATES:
        return False
    created_at = document.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    updated_at = getattr(import_record, "updated_at", None)
    if updated_at is None:
        updated_at = created_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return created_at <= cutoff and updated_at <= cutoff


def expire_eligible_documents(db: Session, storage=None, *, now: datetime | None = None) -> dict:
    """Delete expired R2 originals but retain metadata and any personal Drive backup."""
    settings = get_settings()
    retention_days = settings.document_retention_days
    if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 1:
        raise ValueError("document_retention_days must be a positive integer")
    current_time = now or datetime.now(timezone.utc)
    cutoff = current_time - timedelta(days=retention_days)
    storage = storage or build_object_storage(settings)
    deleted = 0
    skipped = 0
    failures = 0

    documents = db.scalars(select(Document).where(Document.created_at <= cutoff)).all()
    for document in documents:
        import_record = db.scalar(
            select(DocumentImport).where(DocumentImport.document_id == document.id)
        )
        if not is_retention_eligible(document, import_record, cutoff):
            skipped += 1
            continue
        try:
            storage.delete(document.storage_key)
            document.metadata_json = {
                **(document.metadata_json or {}),
                "retention": {
                    "status": "EXPIRED",
                    "expired_at": current_time.isoformat(),
                    "retention_days": retention_days,
                },
            }
            import_record.status = "EXPIRED"
            import_record.revision += 1
            import_record.updated_at = current_time
            db.commit()
            deleted += 1
        except Exception:
            db.rollback()
            failures += 1
    return {"deleted": deleted, "skipped": skipped, "failures": failures}
