"""Render and privately serve candidate-approved Master CVs and variants."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.documents.backup import backup_to_personal_drive
from app.application.documents.rendering import render_document
from app.application.cv_studio.service import CVStudioService
from app.config.settings import get_settings
from app.infrastructure.db.models import CandidateCVApproval, CandidateCVVersion, CandidateProfile, Document
from app.infrastructure.storage.factory import build_object_storage
from app.ports.object_storage import ObjectStorage, StoredObject

logger = logging.getLogger("jobpunter.cv_documents")


def _canonical_hash(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _display_value(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        parts = [_display_value(item) for item in value]
        rendered = [part for part in parts if part]
        return "; ".join(rendered) if rendered else None
    return None


def _renderable_content(version: CandidateCVVersion) -> dict:
    """Convert structured profile-shaped JSON to printable sections without altering stored CV content."""
    content = copy.deepcopy(version.content)
    if not isinstance(content, dict):
        raise DomainError("CV_CONTENT_INVALID", "CV content must be a JSON object.", 422)
    if not content:
        raise DomainError("CV_CONTENT_EMPTY", "CV content cannot be rendered.", 422)

    renderable = dict(content)
    if not isinstance(renderable.get("title"), str) or not renderable["title"].strip():
        renderable["title"] = version.title
    if any(isinstance(renderable.get(key), str) and renderable[key].strip()
           for key in ("rawText", "content", "text", "body")):
        return renderable
    if isinstance(renderable.get("sections"), list):
        return renderable

    sections = []
    for key, value in content.items():
        if key in {"title", "name", "metadata"} or value in (None, "", [], {}):
            continue
        heading = key.replace("_", " ").strip().title()
        items: list[str] = []
        if isinstance(value, dict):
            for field, field_value in value.items():
                displayed = _display_value(field_value)
                if displayed:
                    items.append(f"{field.replace('_', ' ').title()}: {displayed}")
                elif isinstance(field_value, dict):
                    nested = []
                    for nested_key, raw_nested_value in field_value.items():
                        displayed_nested = _display_value(raw_nested_value)
                        if displayed_nested:
                            nested.append(
                                f"{nested_key.replace('_', ' ').title()}: {displayed_nested}"
                            )
                    if nested:
                        items.append(f"{field.replace('_', ' ').title()}: " + "; ".join(nested))
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict):
                    parts = []
                    for field, field_value in entry.items():
                        displayed = _display_value(field_value)
                        if displayed:
                            parts.append(f"{field.replace('_', ' ').title()}: {displayed}")
                    if parts:
                        items.append(" | ".join(parts))
                else:
                    displayed = _display_value(entry)
                    if displayed:
                        items.append(displayed)
        else:
            displayed = _display_value(value)
            if displayed:
                items.append(displayed)
        if items:
            sections.append({"heading": heading, "items": items})
    if not sections:
        raise DomainError("CV_CONTENT_EMPTY", "CV content has no printable text.", 422)
    return {"title": renderable["title"], "sections": sections}


class CVDocumentService:
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
            logger.warning("CV storage initialization failed: %s", type(exc).__name__)
            raise DomainError("OBJECT_STORAGE_UNAVAILABLE", "Private document storage is unavailable.", 503) from exc
        return self._storage

    def _owned_version(self, user_id: UUID, cv_key: str, version_number: int):
        profile = self.db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
        if profile is None:
            raise DomainError("CANDIDATE_PROFILE_NOT_FOUND", "Candidate profile not found.", 404)
        version = self.db.scalar(select(CandidateCVVersion).where(
            CandidateCVVersion.candidate_profile_id == profile.id,
            CandidateCVVersion.cv_key == cv_key,
            CandidateCVVersion.version_number == version_number,
        ))
        if version is None:
            raise DomainError("CV_VERSION_NOT_FOUND", "CV version not found.", 404)
        approval = self.db.scalar(select(CandidateCVApproval).where(
            CandidateCVApproval.candidate_profile_id == profile.id,
            CandidateCVApproval.cv_version_id == version.id,
        ))
        if approval is None:
            raise DomainError("CV_VERSION_NOT_APPROVED", "Approve this exact CV version before downloading it.", 409)
        if (
            _canonical_hash(version.content) != version.content_hash
            or _canonical_hash(version.source_profile_snapshot) != version.source_profile_hash
        ):
            raise DomainError("CV_VERSION_INTEGRITY_INVALID", "CV version provenance failed integrity verification.", 409)
        return profile, version

    def generate(self, user_id: UUID, cv_key: str, version_number: int, output_format: str) -> Document:
        profile, version = self._owned_version(user_id, cv_key, version_number)
        try:
            content, mime_type, extension = render_document(
                _renderable_content(version), "tailored_cv", output_format
            )
        except ValueError as exc:
            raise DomainError("CV_CONTENT_INVALID", str(exc), 422) from exc

        settings = get_settings()
        document_id = uuid4()
        key = (
            f"candidates/{profile.id}/cvs/{version.cv_key}/v{version.version_number}/"
            f"{uuid4().hex}.{extension}"
        )
        expected_checksum = hashlib.sha256(content).hexdigest()
        storage = self._storage_adapter()
        try:
            stored: StoredObject = storage.put(key, content, mime_type)
        except Exception as exc:
            logger.warning("CV document upload failed: %s", type(exc).__name__)
            raise DomainError("DOCUMENT_STORAGE_FAILED", "The CV could not be stored securely.", 502) from exc
        if (
            stored.storage_key != key
            or stored.checksum_sha256 != expected_checksum
            or stored.byte_size != len(content)
        ):
            try:
                storage.delete(key)
            except Exception as exc:
                logger.error("failed to clean up invalid stored CV error_type=%s", type(exc).__name__)
            raise DomainError(
                "DOCUMENT_STORAGE_INTEGRITY_FAILED",
                "Stored CV integrity could not be verified.",
                502,
            )

        drive_backup = backup_to_personal_drive(
            settings,
            document_id=str(document_id),
            filename=f"{version.cv_key}-v{version.version_number}.{extension}",
            content=content,
            content_type=mime_type,
            checksum_sha256=expected_checksum,
        )
        document = Document(
            id=document_id,
            candidate_profile_id=profile.id,
            application_package_id=None,
            application_package_item_id=None,
            document_type="master_cv" if version.cv_type == "MASTER" else "cv_variant",
            filename=f"{version.cv_key}-v{version.version_number}.{extension}",
            mime_type=mime_type,
            storage_key=key,
            checksum_sha256=expected_checksum,
            byte_size=len(content),
            metadata_json={
                "output_format": extension,
                "cv_key": version.cv_key,
                "cv_version_id": str(version.id),
                "cv_version_number": version.version_number,
                "cv_content_hash": version.content_hash,
                "source_profile_revision": version.source_profile_revision,
                "source_profile_hash": version.source_profile_hash,
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
            except Exception as exc:
                logger.error("failed to clean up orphaned CV after metadata failure error_type=%s", type(exc).__name__)
            raise
        return document

    def create_download_url(self, user_id: UUID, cv_key: str, version_number: int, document_id: UUID) -> dict:
        profile, version = self._owned_version(user_id, cv_key, version_number)
        document = self.db.scalar(select(Document).where(
            Document.id == document_id,
            Document.candidate_profile_id == profile.id,
            Document.application_package_id.is_(None),
            Document.document_type.in_({"master_cv", "cv_variant"}),
        ))
        if (
            document is None
            or not isinstance(document.metadata_json, dict)
            or document.metadata_json.get("cv_version_id") != str(version.id)
        ):
            raise DomainError("DOCUMENT_NOT_FOUND", "CV document not found.", 404)
        expected_prefix = f"candidates/{profile.id}/cvs/{version.cv_key}/v{version.version_number}/"
        if not document.storage_key.startswith(expected_prefix):
            raise DomainError(
                "DOCUMENT_STORAGE_REFERENCE_INVALID",
                "CV storage reference failed ownership validation.",
                409,
            )
        ttl = get_settings().storage_download_url_ttl_seconds
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= 3600:
            raise DomainError("DOCUMENT_DOWNLOAD_TTL_INVALID", "Document download link lifetime is misconfigured.", 503)
        try:
            url = self._storage_adapter().create_download_url(document.storage_key, ttl)
        except Exception as exc:
            logger.warning("CV download URL creation failed: %s", type(exc).__name__)
            raise DomainError("DOCUMENT_STORAGE_FAILED", "A secure CV download link could not be created.", 502) from exc
        return {
            "document_id": str(document.id),
            "filename": document.filename,
            "mime_type": document.mime_type,
            "checksum_sha256": document.checksum_sha256,
            "byte_size": document.byte_size,
            "expires_in_seconds": ttl,
            "download_url": url,
            "cv_key": version.cv_key,
            "cv_version_number": version.version_number,
        }
