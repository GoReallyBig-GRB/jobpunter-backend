"""Best-effort personal Drive backup; failures never invalidate the R2 original."""
import logging
from datetime import datetime, timezone

from app.config.settings import Settings
from app.infrastructure.storage.google_drive_backup import GoogleDriveBackupAdapter

logger = logging.getLogger("jobpunter.document_backup")


def backup_to_personal_drive(
    settings: Settings,
    *,
    document_id: str,
    filename: str,
    content: bytes,
    content_type: str,
    checksum_sha256: str,
) -> dict:
    attempted_at = datetime.now(timezone.utc).isoformat()
    if not settings.google_drive_backup_enabled:
        return {"status": "DISABLED", "attempted_at": attempted_at}

    required = (
        settings.google_drive_client_id,
        settings.google_drive_client_secret,
        settings.google_drive_refresh_token,
    )
    if not all(isinstance(value, str) and value.strip() for value in required):
        return {
            "status": "FAILED",
            "error_code": "BACKUP_NOT_CONFIGURED",
            "attempted_at": attempted_at,
        }

    try:
        adapter = GoogleDriveBackupAdapter(
            client_id=settings.google_drive_client_id,
            client_secret=settings.google_drive_client_secret,
            refresh_token=settings.google_drive_refresh_token,
            folder_id=settings.google_drive_folder_id,
        )
        result = adapter.backup(
            document_id=document_id,
            filename=filename,
            content=content,
            content_type=content_type,
            checksum_sha256=checksum_sha256,
        )
        return {
            "status": "BACKED_UP",
            "file_id": result.file_id,
            "attempted_at": attempted_at,
        }
    except Exception as exc:
        logger.warning("personal Drive backup failed: %s", type(exc).__name__)
        return {
            "status": "FAILED",
            "error_code": "BACKUP_PROVIDER_ERROR",
            "attempted_at": attempted_at,
        }
