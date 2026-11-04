"""Dependency-aware cleanup for terminal, unreferenced uploaded documents.

Run from a scheduled backend job. Candidate evidence and application-package artifacts are
intentionally excluded. Personal Drive backup copies are not deleted by this worker.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.infrastructure.db.models import AuditEvent, Document, DocumentImport
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

    candidate_ids = list(db.scalars(
        select(Document.id)
        .where(Document.created_at <= cutoff)
        .order_by(Document.id)
    ).all())
    for document_id in candidate_ids:
        # Serialize retention against candidate import/confirmation/save requests.
        # Lock one document/import pair at a time so the batch does not hold a
        # table's entire eligible set while deleting remote objects.
        document = db.scalar(
            select(Document)
            .where(Document.id == document_id, Document.created_at <= cutoff)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if document is None:
            skipped += 1
            continue
        import_record = db.scalar(
            select(DocumentImport)
            .where(DocumentImport.document_id == document.id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if not is_retention_eligible(document, import_record, cutoff):
            db.rollback()
            skipped += 1
            continue
        remote_deleted = False
        try:
            storage.delete(document.storage_key)
            remote_deleted = True
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
            db.add(_retention_audit("DOCUMENT_RETENTION_EXPIRED", document, "EXPIRED", retention_days))
            db.commit()
            deleted += 1
        except Exception as exc:
            db.rollback()
            failures += 1
            _record_failure(db, document_id, retention_days, remote_deleted, exc)
    return {"deleted": deleted, "skipped": skipped, "failures": failures}


def _retention_audit(event_type: str, document: Document, to_status: str, retention_days: int, **details) -> AuditEvent:
    # Only identifiers and safe categories: never the storage key, filename or content.
    return AuditEvent(
        event_type=event_type,
        resource_type="DOCUMENT",
        resource_id=document.id,
        actor_type="SYSTEM",
        to_status=to_status,
        details={"retention_days": retention_days, **details},
    )


def _record_failure(db: Session, document_id, retention_days: int, remote_deleted: bool, exc: Exception) -> None:
    """Make a failed cleanup detectable. `remote_object_deleted=True` flags a missing file whose
    metadata was not updated, which operators must reconcile."""
    try:
        db.add(AuditEvent(
            event_type="DOCUMENT_RETENTION_FAILED",
            resource_type="DOCUMENT",
            resource_id=document_id,
            actor_type="SYSTEM",
            to_status="FAILED",
            reason=type(exc).__name__,
            details={"retention_days": retention_days, "remote_object_deleted": remote_deleted},
        ))
        db.commit()
    except Exception:
        db.rollback()
