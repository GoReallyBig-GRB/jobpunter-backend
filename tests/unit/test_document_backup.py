from app.application.documents.backup import backup_to_personal_drive
from app.config.settings import Settings


def test_personal_drive_backup_is_disabled_by_default():
    result = backup_to_personal_drive(
        Settings(),
        document_id="doc-1",
        filename="cv.pdf",
        content=b"%PDF-test",
        content_type="application/pdf",
        checksum_sha256="abc123",
    )
    assert result["status"] == "DISABLED"
    assert "file_id" not in result


def test_enabled_personal_drive_backup_fails_closed_when_credentials_missing():
    result = backup_to_personal_drive(
        Settings(google_drive_backup_enabled=True),
        document_id="doc-1",
        filename="cv.pdf",
        content=b"%PDF-test",
        content_type="application/pdf",
        checksum_sha256="abc123",
    )
    assert result["status"] == "FAILED"
    assert result["error_code"] == "BACKUP_NOT_CONFIGURED"
