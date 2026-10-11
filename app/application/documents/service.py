"""Generate, persist, and privately serve documents for candidate-owned packages."""
from __future__ import annotations

import logging
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.application_packages.review import (
    APPROVED_STATUS,
    READY_STATUS,
    ApplicationPackageReviewService,
)
from app.application.documents.rendering import render_document
from app.application.documents.backup import backup_to_personal_drive
from app.config.settings import get_settings
from app.infrastructure.db.models import Application, ApplicationPackage, Document
from app.infrastructure.storage.factory import build_object_storage
from app.ports.object_storage import ObjectStorage, StoredObject

logger = logging.getLogger("jobpunter.documents")


class DocumentGenerationService:
    def __init__(self, db: Session, storage: ObjectStorage | None = None):
        self.db = db
        self._storage = storage

    def _storage_adapter(self) -> ObjectStorage:
        if self._storage is not None:
            return self._storage
        try:
            self._storage = build_object_storage(get_settings())
        except DomainError:
            raise
        except Exception as exc:
            logger.warning("document storage initialization failed: %s", type(exc).__name__)
            raise DomainError(
                "OBJECT_STORAGE_UNAVAILABLE",
                "Private document storage is unavailable.",
                503,
            ) from exc
        return self._storage

    def _ready_package(
        self, user_id: UUID, application_id: UUID, package_id: UUID
    ) -> tuple[object, ApplicationPackage, list]:
        review = ApplicationPackageReviewService(self.db)
        profile, package, items = review._owned_package(user_id, application_id, package_id)
        if package.status not in {READY_STATUS, APPROVED_STATUS}:
            raise DomainError(
                "APPLICATION_PACKAGE_NOT_READY",
                "Only a package prepared for review or approved may be rendered.",
                409,
                {"package_status": package.status},
            )
        blockers = review.readiness(profile.id, package.id, items, user_id)
        if blockers:
            raise DomainError(
                "APPLICATION_PACKAGE_NOT_READY",
                "Package readiness checks no longer pass.",
                409,
                {"blockers": blockers},
            )
        return profile, package, items

    def generate(
        self,
        user_id: UUID,
        application_id: UUID,
        package_id: UUID,
        artifact_type: str,
        output_format: str,
    ) -> Document:
        profile, package, items = self._ready_package(user_id, application_id, package_id)
        item = next((row for row in items if row.artifact_type == artifact_type), None)
        if item is None:
            raise DomainError(
                "APPLICATION_PACKAGE_ITEM_NOT_FOUND",
                "The requested artifact is not part of this package version.",
                404,
            )
        try:
            content, mime_type, extension = render_document(item.content, artifact_type, output_format)
        except ValueError as exc:
            raise DomainError("DOCUMENT_CONTENT_INVALID", str(exc), 422) from exc

        settings = get_settings()
        key = (
            f"candidates/{profile.id}/applications/{application_id}/packages/{package.id}/"
            f"{artifact_type}/{uuid4().hex}.{extension}"
        )
        expected_checksum = sha256(content).hexdigest()
        storage = self._storage_adapter()
        try:
            stored: StoredObject = storage.put(key, content, mime_type)
        except Exception as exc:
            logger.warning("document upload failed: %s", type(exc).__name__)
            raise DomainError(
                "DOCUMENT_STORAGE_FAILED",
                "The generated document could not be stored securely.",
                502,
            ) from exc
        if (
            stored.storage_key != key
            or stored.checksum_sha256 != expected_checksum
            or stored.byte_size != len(content)
        ):
            try:
                storage.delete(key)
            except Exception:
                logger.exception("failed to clean up invalid stored document")
            raise DomainError(
                "DOCUMENT_STORAGE_INTEGRITY_FAILED",
                "Stored document integrity could not be verified.",
                502,
            )

        document_id = uuid4()
        drive_backup = backup_to_personal_drive(
            settings,
            document_id=str(document_id),
            filename=f"{artifact_type.replace('_', '-')}-v{package.version_number}.{extension}",
            content=content,
            content_type=mime_type,
            checksum_sha256=expected_checksum,
        )
        document = Document(
            id=document_id,
            candidate_profile_id=profile.id,
            application_package_id=package.id,
            application_package_item_id=item.id,
            document_type=artifact_type,
            filename=f"{artifact_type.replace('_', '-')}-v{package.version_number}.{extension}",
            mime_type=mime_type,
            storage_key=key,
            checksum_sha256=expected_checksum,
            byte_size=len(content),
            metadata_json={
                "output_format": extension,
                "package_version_number": package.version_number,
                "artifact_content_hash": item.content_hash,
                "google_drive_backup": drive_backup,
            },
        )
        try:
            self.db.add(document)
            self.db.commit()
            self.db.refresh(document)
        except Exception:
            self.db.rollback()
            try:
                storage.delete(key)
            except Exception:
                logger.exception("failed to clean up orphaned document after metadata failure")
            raise
        return document

    def create_download_url(
        self,
        user_id: UUID,
        application_id: UUID,
        package_id: UUID,
        document_id: UUID,
    ) -> dict:
        profile, package, _items = self._ready_package(user_id, application_id, package_id)
        document = self.db.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.candidate_profile_id == profile.id,
                Document.application_package_id == package.id,
            )
        )
        if document is None:
            raise DomainError("DOCUMENT_NOT_FOUND", "Document not found", 404)
        expected_prefix = (
            f"candidates/{profile.id}/applications/{application_id}/packages/{package.id}/"
        )
        if not document.storage_key.startswith(expected_prefix):
            raise DomainError(
                "DOCUMENT_STORAGE_REFERENCE_INVALID",
                "Document storage reference failed ownership validation.",
                409,
            )
        ttl = get_settings().storage_download_url_ttl_seconds
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= 3600:
            raise DomainError(
                "DOCUMENT_DOWNLOAD_TTL_INVALID",
                "Document download link lifetime is misconfigured.",
                503,
            )
        try:
            url = self._storage_adapter().create_download_url(document.storage_key, ttl)
        except Exception as exc:
            logger.warning("document download URL creation failed: %s", type(exc).__name__)
            raise DomainError(
                "DOCUMENT_STORAGE_FAILED",
                "A secure document download link could not be created.",
                502,
            ) from exc
        return {
            "document_id": str(document.id),
            "filename": document.filename,
            "mime_type": document.mime_type,
            "checksum_sha256": document.checksum_sha256,
            "byte_size": document.byte_size,
            "expires_in_seconds": ttl,
            "download_url": url,
            "package_version_number": package.version_number,
        }
