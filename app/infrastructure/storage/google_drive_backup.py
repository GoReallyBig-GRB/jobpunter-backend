"""Optional private Google Drive backup adapter using a personal OAuth refresh token."""
from dataclasses import dataclass
import hashlib
from io import BytesIO


@dataclass(frozen=True, slots=True)
class DriveBackupResult:
    file_id: str


class GoogleDriveBackupAdapter:
    """Copy private document bytes to a configured personal Drive folder without sharing them."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        folder_id: str | None = None,
        service=None,
    ):
        for name, value in (
            ("client_id", client_id),
            ("client_secret", client_secret),
            ("refresh_token", refresh_token),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        self._folder_id = folder_id.strip() if isinstance(folder_id, str) and folder_id.strip() else None
        if service is None:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build

            credentials = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=["https://www.googleapis.com/auth/drive.file"],
            )
            service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self._service = service

    def backup(
        self,
        *,
        document_id: str,
        filename: str,
        content: bytes,
        content_type: str,
        checksum_sha256: str,
    ) -> DriveBackupResult:
        if not all(isinstance(value, str) and value.strip() for value in (
            document_id, filename, content_type, checksum_sha256
        )):
            raise ValueError("document metadata is required for backup")
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")

        from googleapiclient.http import MediaIoBaseUpload

        properties = {
            "jobpunterDocumentId": document_id,
            "jobpunterSha256": checksum_sha256,
        }
        query = (
            "appProperties has { key='jobpunterDocumentId' and "
            f"value='{document_id}' }} and trashed = false"
        )
        existing = self._service.files().list(
            q=query, spaces="drive", pageSize=1, fields="files(id)"
        ).execute().get("files", [])
        media = MediaIoBaseUpload(BytesIO(content), mimetype=content_type, resumable=False)
        metadata = {"name": filename, "appProperties": properties}
        if self._folder_id:
            metadata["parents"] = [self._folder_id]
        if existing:
            file_id = existing[0]["id"]
            self._service.files().update(
                fileId=file_id,
                body={"name": filename, "appProperties": properties},
                media_body=media,
                fields="id",
            ).execute()
        else:
            file_id = self._service.files().create(
                body=metadata, media_body=media, fields="id"
            ).execute()["id"]
        return DriveBackupResult(file_id=file_id)


    def restore(
        self, *, file_id: str, document_id: str, checksum_sha256: str
    ) -> bytes:
        """Download only a backup whose private Drive metadata matches the document."""
        if not all(isinstance(value, str) and value.strip() for value in (
            file_id, document_id, checksum_sha256
        )):
            raise ValueError("backup identity and checksum are required")
        metadata = self._service.files().get(
            fileId=file_id, fields="id,appProperties,trashed"
        ).execute()
        properties = metadata.get("appProperties") if isinstance(metadata, dict) else None
        if (
            not isinstance(metadata, dict)
            or metadata.get("id") != file_id
            or metadata.get("trashed") is True
            or not isinstance(properties, dict)
            or properties.get("jobpunterDocumentId") != document_id
            or properties.get("jobpunterSha256") != checksum_sha256
        ):
            raise ValueError("backup identity metadata does not match the requested document")

        from googleapiclient.http import MediaIoBaseDownload

        output = BytesIO()
        downloader = MediaIoBaseDownload(output, self._service.files().get_media(fileId=file_id))
        done = False
        while not done:
            _status, done = downloader.next_chunk()
        content = output.getvalue()
        if hashlib.sha256(content).hexdigest() != checksum_sha256:
            raise ValueError("backup content checksum does not match the requested document")
        return content
