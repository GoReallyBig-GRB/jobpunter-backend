import hashlib

import pytest

from app.infrastructure.storage.google_drive_backup import GoogleDriveBackupAdapter


class _Request:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class _Files:
    def __init__(self, metadata, media_request=None):
        self.metadata = metadata
        self.media_request = media_request

    def get(self, fileId, fields):
        return _Request(self.metadata)

    def get_media(self, fileId):
        return self.media_request


class _DriveService:
    def __init__(self, files):
        self._files = files

    def files(self):
        return self._files


def test_google_drive_restore_verifies_document_identity_and_checksum(monkeypatch):
    import googleapiclient.http

    content = b"verified private document"
    checksum = hashlib.sha256(content).hexdigest()
    metadata = {
        "id": "drive-file-1",
        "trashed": False,
        "appProperties": {
            "jobpunterDocumentId": "document-1",
            "jobpunterSha256": checksum,
        },
    }

    class FakeDownloader:
        def __init__(self, output, request):
            output.write(content)

        def next_chunk(self):
            return None, True

    monkeypatch.setattr(googleapiclient.http, "MediaIoBaseDownload", FakeDownloader)
    adapter = GoogleDriveBackupAdapter(
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
        service=_DriveService(_Files(metadata, media_request=object())),
    )
    assert adapter.restore(
        file_id="drive-file-1",
        document_id="document-1",
        checksum_sha256=checksum,
    ) == content


@pytest.mark.parametrize(
    ("metadata", "expected_checksum"),
    [
        (
            {"id": "drive-file-1", "trashed": True, "appProperties": {
                "jobpunterDocumentId": "document-1", "jobpunterSha256": "abc",
            }},
            "abc",
        ),
        (
            {"id": "drive-file-1", "trashed": False, "appProperties": {
                "jobpunterDocumentId": "different-document", "jobpunterSha256": "abc",
            }},
            "abc",
        ),
        (
            {"id": "drive-file-1", "trashed": False, "appProperties": {
                "jobpunterDocumentId": "document-1", "jobpunterSha256": "different-checksum",
            }},
            "abc",
        ),
    ],
)
def test_google_drive_restore_rejects_untrusted_backup_metadata(metadata, expected_checksum):
    adapter = GoogleDriveBackupAdapter(
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
        service=_DriveService(_Files(metadata)),
    )
    with pytest.raises(ValueError):
        adapter.restore(
            file_id="drive-file-1",
            document_id="document-1",
            checksum_sha256=expected_checksum,
        )
