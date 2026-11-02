"""Storage usage observed from document metadata (PRD §50 usage monitoring and cost alerts).

Usage is derived from `documents.byte_size`; it is not provider-billed usage.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.infrastructure.db.models import Document


def storage_alert_level(used_bytes: int, warning_bytes: int | None, critical_bytes: int | None) -> str:
    if warning_bytes is None and critical_bytes is None:
        return "NOT_CONFIGURED"
    if critical_bytes is not None and used_bytes >= critical_bytes:
        return "CRITICAL"
    if warning_bytes is not None and used_bytes >= warning_bytes:
        return "WARNING"
    return "OK"


def collect_storage_usage(db: Session) -> dict:
    settings = get_settings()
    expired = Document.metadata_json["retention"]["status"].as_string() == "EXPIRED"
    live_filter = ~func.coalesce(expired, False)
    rows = db.execute(
        select(Document.document_type, func.count(Document.id), func.coalesce(func.sum(Document.byte_size), 0))
        .where(live_filter).group_by(Document.document_type)
    ).all()
    expired_count = int(db.scalar(select(func.count(Document.id)).where(func.coalesce(expired, False))) or 0)
    live_bytes = sum(int(r[2]) for r in rows)
    warning, critical = settings.storage_usage_warning_bytes, settings.storage_usage_critical_bytes
    return {
        "basis": "DATABASE_METADATA",
        "provider_billed_usage": "NOT_MEASURED",
        "retention_days": settings.document_retention_days,
        "live": {
            "documents": sum(int(r[1]) for r in rows),
            "bytes": live_bytes,
            "bytes_by_document_type": {str(r[0]): int(r[2]) for r in rows},
        },
        "expired_documents": expired_count,
        "alert": {
            "level": storage_alert_level(live_bytes, warning, critical),
            "warning_bytes": warning,
            "critical_bytes": critical,
        },
    }
