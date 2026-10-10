"""Validated candidate-private document import, extraction, and preview."""
from __future__ import annotations

import logging
import re
import zipfile
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

from docx import Document as DocxDocument
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.config.settings import get_settings
from app.infrastructure.db.models import CandidateProfile, Document, DocumentImport
from app.infrastructure.storage.google_cloud_storage import GoogleCloudStorageAdapter
from app.ports.object_storage import ObjectStorage, StoredObject

logger = logging.getLogger("jobpunter.document_imports")
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_PDF_PAGES = 200
MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}
GENERIC_MIME_TYPES = {"application/octet-stream", "binary/octet-stream"}


def validate_document_upload(filename: str, content: bytes, content_type: str) -> tuple[str, str]:
    if not isinstance(content, bytes) or not content:
        raise DomainError("DOCUMENT_EMPTY", "Uploaded document is empty.", 422)
    if len(content) > MAX_DOCUMENT_BYTES:
        raise DomainError("DOCUMENT_TOO_LARGE", "Uploaded document exceeds the 10 MB limit.", 413)
    extension = Path(filename or "").suffix.lower()
    mime_type = MIME_BY_EXTENSION.get(extension)
    if mime_type is None:
        raise DomainError(
            "DOCUMENT_FORMAT_UNSUPPORTED",
            "Only PDF, DOCX, and TXT files are supported.",
            415,
        )
    supplied_type = (content_type or "").split(";", 1)[0].strip().lower()
    if supplied_type and supplied_type not in GENERIC_MIME_TYPES | {mime_type}:
        raise DomainError(
            "DOCUMENT_CONTENT_TYPE_MISMATCH",
            "Uploaded content type does not match the file extension.",
            415,
        )
    if extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise DomainError("DOCUMENT_SIGNATURE_INVALID", "File is not a valid PDF upload.", 422)
    elif extension == ".docx":
        if not zipfile.is_zipfile(BytesIO(content)):
            raise DomainError("DOCUMENT_SIGNATURE_INVALID", "File is not a valid DOCX upload.", 422)
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                    raise DomainError("DOCUMENT_SIGNATURE_INVALID", "File is not a valid DOCX document.", 422)
                if sum(info.file_size for info in archive.infolist()) > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise DomainError("DOCUMENT_TOO_LARGE", "DOCX expands beyond the safe processing limit.", 413)
        except zipfile.BadZipFile as exc:
            raise DomainError("DOCUMENT_SIGNATURE_INVALID", "File is not a valid DOCX upload.", 422) from exc
    else:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DomainError("DOCUMENT_ENCODING_UNSUPPORTED", "TXT uploads must use UTF-8 encoding.", 422) from exc
        if "\x00" in text:
            raise DomainError("DOCUMENT_ENCODING_UNSUPPORTED", "TXT uploads contain unsupported binary data.", 422)
    return extension, mime_type


def extract_document_text(extension: str, content: bytes) -> str:
    try:
        if extension == ".txt":
            text = content.decode("utf-8-sig")
        elif extension == ".docx":
            document = DocxDocument(BytesIO(content))
            parts = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
            for table in document.tables:
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
            text = "\n".join(parts)
        elif extension == ".pdf":
            reader = PdfReader(BytesIO(content), strict=True)
            if reader.is_encrypted:
                raise DomainError("DOCUMENT_ENCRYPTED", "Password-protected PDFs are not supported.", 422)
            if len(reader.pages) > MAX_PDF_PAGES:
                raise DomainError("DOCUMENT_PAGE_LIMIT_EXCEEDED", "PDF exceeds the 200-page processing limit.", 413)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            raise DomainError("DOCUMENT_FORMAT_UNSUPPORTED", "Only PDF, DOCX, and TXT files are supported.", 415)
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError("DOCUMENT_PARSE_FAILED", "The uploaded document could not be parsed.", 422) from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise DomainError(
            "DOCUMENT_TEXT_NOT_EXTRACTABLE",
            "No selectable text was found. Scanned-image OCR is not supported in this import flow.",
            422,
        )
    return text


def _safe_filename(filename: str, extension: str) -> str:
    name = Path(filename or "").name.replace("\x00", "").strip()
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)
    if not name or name in {".", ".."}:
        name = f"uploaded-document{extension}"
    return name[:255]


class DocumentImportService:
    def __init__(self, db: Session, storage: ObjectStorage | None = None):
        self.db = db
        self._storage = storage

    def _profile(self, user_id: UUID) -> CandidateProfile:
        profile = self.db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
        if profile is None:
            raise DomainError("CANDIDATE_PROFILE_NOT_FOUND", "Candidate profile not found", 404)
        return profile

    def _storage_adapter(self) -> ObjectStorage:
        if self._storage is not None:
            return self._storage
        bucket = get_settings().google_cloud_storage_bucket
        if not bucket:
            raise DomainError(
                "OBJECT_STORAGE_NOT_CONFIGURED",
                "Private document storage is not configured.",
                503,
            )
        try:
            self._storage = GoogleCloudStorageAdapter(bucket)
        except Exception as exc:
            logger.warning("document import storage initialization failed: %s", type(exc).__name__)
            raise DomainError("OBJECT_STORAGE_UNAVAILABLE", "Private document storage is unavailable.", 503) from exc
        return self._storage

    def upload(
        self, user_id: UUID, filename: str, content_type: str, content: bytes
    ) -> DocumentImport:
        profile = self._profile(user_id)
        extension, mime_type = validate_document_upload(filename, content, content_type)
        safe_name = _safe_filename(filename, extension)
        document_id = uuid4()
        storage_key = f"candidates/{profile.id}/imports/{document_id}/{safe_name}"
        storage = self._storage_adapter()
        checksum = sha256(content).hexdigest()
        try:
            stored: StoredObject = storage.put(storage_key, content, mime_type)
        except Exception as exc:
            logger.warning("document import upload failed: %s", type(exc).__name__)
            raise DomainError("DOCUMENT_STORAGE_FAILED", "Uploaded document could not be stored securely.", 502) from exc
        if (
            stored.storage_key != storage_key
            or stored.checksum_sha256 != checksum
            or stored.byte_size != len(content)
        ):
            try:
                storage.delete(storage_key)
            except Exception:
                logger.exception("failed to clean up invalid uploaded document")
            raise DomainError("DOCUMENT_STORAGE_INTEGRITY_FAILED", "Stored document integrity could not be verified.", 502)

        document = Document(
            id=document_id,
            candidate_profile_id=profile.id,
            document_type="import_source",
            filename=safe_name,
            mime_type=mime_type,
            storage_key=storage_key,
            checksum_sha256=checksum,
            byte_size=len(content),
            metadata_json={"original_content_type": content_type, "extension": extension},
        )
        record = DocumentImport(
            candidate_profile_id=profile.id,
            document_id=document_id,
            status="UPLOADED",
            extracted_text=None,
            extracted_fields={},
            conflicts=[],
            revision=1,
            error_code=None,
        )
        try:
            self.db.add_all([document, record])
            self.db.commit()
            self.db.refresh(record)
        except Exception:
            self.db.rollback()
            try:
                storage.delete(storage_key)
            except Exception:
                logger.exception("failed to clean up uploaded object after metadata failure")
            raise
        return record

    def _owned_import(self, user_id: UUID, import_id: UUID) -> tuple[CandidateProfile, DocumentImport, Document]:
        profile = self._profile(user_id)
        record = self.db.scalar(
            select(DocumentImport).where(
                DocumentImport.id == import_id,
                DocumentImport.candidate_profile_id == profile.id,
            ).with_for_update()
        )
        if record is None:
            raise DomainError("DOCUMENT_IMPORT_NOT_FOUND", "Document import not found", 404)
        document = self.db.scalar(select(Document).where(
            Document.id == record.document_id,
            Document.candidate_profile_id == profile.id,
        ))
        if document is None:
            raise DomainError("DOCUMENT_NOT_FOUND", "Imported document not found", 404)
        return profile, record, document

    def parse(self, user_id: UUID, import_id: UUID) -> DocumentImport:
        _profile, record, document = self._owned_import(user_id, import_id)
        if record.status in {"PARSED", "PREVIEWED", "CONFLICTS_IDENTIFIED", "CONFIRMED", "SAVED"}:
            return record
        if record.status != "UPLOADED":
            raise DomainError("DOCUMENT_IMPORT_STATE_CONFLICT", "This import cannot be parsed in its current state.", 409)
        try:
            content = self._storage_adapter().get(document.storage_key)
            if len(content) != document.byte_size or sha256(content).hexdigest() != document.checksum_sha256:
                raise DomainError("DOCUMENT_INTEGRITY_FAILED", "Uploaded document failed integrity verification.", 409)
            extension = document.metadata_json.get("extension")
            record.extracted_text = extract_document_text(extension, content)
            record.status = "PARSED"
            record.error_code = None
        except DomainError as exc:
            record.status = "FAILED"
            record.error_code = exc.code
            record.updated_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            self.db.commit()
            raise
        except Exception as exc:
            record.status = "FAILED"
            record.error_code = "DOCUMENT_PARSE_FAILED"
            self.db.commit()
            logger.warning("document import parse failed: %s", type(exc).__name__)
            raise DomainError("DOCUMENT_PARSE_FAILED", "The uploaded document could not be parsed.", 422) from exc
        record.revision += 1
        self.db.commit()
        self.db.refresh(record)
        return record

    def preview(self, user_id: UUID, import_id: UUID) -> DocumentImport:
        _profile, record, _document = self._owned_import(user_id, import_id)
        if record.status not in {"PARSED", "PREVIEWED", "CONFLICTS_IDENTIFIED"}:
            raise DomainError("DOCUMENT_IMPORT_NOT_PREVIEWABLE", "Parse the document before requesting its preview.", 409)
        if record.status == "PARSED":
            record.status = "PREVIEWED"
            record.revision += 1
            self.db.commit()
            self.db.refresh(record)
        return record
