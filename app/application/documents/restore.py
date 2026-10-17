"""Candidate-authorized recovery of private document bytes from verified backups."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.config.settings import get_settings
from app.infrastructure.db.models import CandidateProfile, Document
from app.infrastructure.storage.factory import build_object_storage
from app.infrastructure.storage.google_drive_backup import GoogleDriveBackupAdapter
from app.ports.object_storage import ObjectStorage, StoredObject

logger = logging.getLogger("jobpunter.document_restore")


class DocumentRestoreService:
    def __init__(self, db: Session, storage: ObjectStorage | None = None, backup_adapter=None):
        self.db = db
        self._storage = storage
        self._backup = backup_adapter

    def _storage_adapter(self) -> ObjectStorage:
        if self._storage is not None:
            return self._storage
        try:
            self._storage = build_object_storage(get_settings())
        except DomainError:
            raise
        except Exception as exc:
            logger.warning("document restore storage initialization failed: %s", type(exc).__name__)
            raise DomainError("OBJECT_STORAGE_UNAVAILABLE", "Private document storage is unavailable.", 503) from exc
        return self._storage

    def _backup_adapter(self):
        if self._backup is not None:
            return self._backup
        settings = get_settings()
        # The backup toggle controls creating new backups, not restoring a backup
        # that was already recorded for this document.
        required = (
            settings.google_drive_client_id,
            settings.google_drive_client_secret,
            settings.google_drive_refresh_token,
        )
        if not all(isinstance(value, str) and value.strip() for value in required):
            raise DomainError("DOCUMENT_RESTORE_UNAVAILABLE", "Personal Drive backup credentials are unavailable.", 503)
        try:
            self._backup = GoogleDriveBackupAdapter(
                client_id=settings.google_drive_client_id,
                client_secret=settings.google_drive_client_secret,
                refresh_token=settings.google_drive_refresh_token,
                folder_id=settings.google_drive_folder_id,
            )
        except Exception as exc:
            logger.warning("document restore backup initialization failed: %s", type(exc).__name__)
            raise DomainError("DOCUMENT_RESTORE_UNAVAILABLE", "Personal Drive backup could not be initialized.", 503) from exc
        return self._backup

    def restore(self, user_id: UUID, document_id: UUID) -> dict:
        profile = self.db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
        if profile is None:
            raise DomainError("CANDIDATE_PROFILE_NOT_FOUND", "Candidate profile not found.", 404)
        document = self.db.scalar(select(Document).where(
            Document.id == document_id,
            Document.candidate_profile_id == profile.id,
        ))
        if document is None:
            raise DomainError("DOCUMENT_NOT_FOUND", "Document not found.", 404)

        storage = self._storage_adapter()
        existing_content = None
        try:
            existing_content = storage.get(document.storage_key)
        except Exception:
            # The provider may report a missing object or an unavailable read. A
            # verified backup is still required; a failed write remains a hard error.
            existing_content = None
        if (
            isinstance(existing_content, bytes)
            and len(existing_content) == document.byte_size
            and hashlib.sha256(existing_content).hexdigest() == document.checksum_sha256
        ):
            return {
                "document_id": str(document.id),
                "status": "ALREADY_AVAILABLE",
                "checksum_sha256": document.checksum_sha256,
                "byte_size": document.byte_size,
            }

        metadata = document.metadata_json if isinstance(document.metadata_json, dict) else {}
        backup = metadata.get("google_drive_backup")
        if (
            not isinstance(backup, dict)
            or backup.get("status") != "BACKED_UP"
            or not isinstance(backup.get("file_id"), str)
            or not backup["file_id"].strip()
        ):
            raise DomainError("DOCUMENT_RESTORE_UNAVAILABLE", "No verified backup is recorded for this document.", 409)
        try:
            content = self._backup_adapter().restore(
                file_id=backup["file_id"],
                document_id=str(document.id),
                checksum_sha256=document.checksum_sha256,
            )
        except DomainError:
            raise
        except Exception as exc:
            logger.warning("document backup restore failed: %s", type(exc).__name__)
            raise DomainError("DOCUMENT_RESTORE_FAILED", "The stored backup could not be retrieved and verified.", 502) from exc

        if (
            not isinstance(content, bytes)
            or len(content) != document.byte_size
            or hashlib.sha256(content).hexdigest() != document.checksum_sha256
        ):
            raise DomainError(
                "DOCUMENT_BACKUP_INTEGRITY_INVALID",
                "Backup content does not match the document checksum and size.",
                409,
            )

        if existing_content is not None:
            try:
                storage.delete(document.storage_key)
            except Exception as exc:
                logger.warning("corrupt document cleanup failed: %s", type(exc).__name__)
                raise DomainError("DOCUMENT_RESTORE_FAILED", "The damaged object could not be safely replaced.", 502) from exc
        try:
            stored: StoredObject = storage.put(document.storage_key, content, document.mime_type)
        except Exception as exc:
            logger.warning("restored document upload failed: %s", type(exc).__name__)
            raise DomainError("DOCUMENT_RESTORE_FAILED", "Verified backup bytes could not be restored to private storage.", 502) from exc
        if (
            stored.storage_key != document.storage_key
            or stored.checksum_sha256 != document.checksum_sha256
            or stored.byte_size != document.byte_size
        ):
            try:
                storage.delete(document.storage_key)
            except Exception as exc:
                logger.error("failed to clean up invalid restored document error_type=%s", type(exc).__name__)
            raise DomainError(
                "DOCUMENT_RESTORE_INTEGRITY_FAILED",
                "Restored storage metadata failed integrity verification.",
                502,
            )

        restored_at = datetime.now(timezone.utc)
        document.metadata_json = {
            **metadata,
            "restore": {
                "status": "RESTORED",
                "restored_at": restored_at.isoformat(),
                "source": "GOOGLE_DRIVE_BACKUP",
                "checksum_sha256": document.checksum_sha256,
            },
        }
        try:
            self.db.commit()
            self.db.refresh(document)
        except Exception:
            self.db.rollback()
            # The verified bytes remain at the immutable key and are safe to retry.
            raise
        return {
            "document_id": str(document.id),
            "status": "RESTORED",
            "checksum_sha256": document.checksum_sha256,
            "byte_size": document.byte_size,
            "restored_at": restored_at.isoformat(),
        }
