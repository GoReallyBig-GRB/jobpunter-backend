import json

import pytest
from cryptography.fernet import Fernet

from app.application.notifications.smtp_config import (
    decrypt_smtp_password,
    encrypt_smtp_password,
    smtp_config_fingerprint,
    smtp_config_response,
)


def test_smtp_password_is_encrypted_and_never_returned(monkeypatch):
    monkeypatch.setenv("JOBPUNTER_SMTP_SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    encrypted = encrypt_smtp_password("private-password")
    assert encrypted is not None
    assert encrypted != "private-password"
    assert decrypt_smtp_password(encrypted) == "private-password"


def test_smtp_password_encryption_fails_closed_without_key(monkeypatch):
    monkeypatch.delenv("JOBPUNTER_SMTP_SECRET_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="smtp_secret_encryption_key_not_configured"):
        encrypt_smtp_password("private-password")


def test_smtp_config_fingerprint_does_not_depend_on_secret_material():
    first = smtp_config_fingerprint({"host": "smtp.example.test", "password": "one"})
    second = smtp_config_fingerprint({"host": "smtp.example.test", "password": "two"})
    assert first == second


def test_smtp_configuration_response_excludes_encrypted_password():
    class Row:
        id = "id"
        version = 3
        status = "DRAFT"
        provider_name = "test"
        host = "smtp.example.test"
        port = 587
        use_tls = True
        use_ssl = False
        username = "user"
        encrypted_password = "encrypted-secret"
        from_email = "noreply@example.test"
        event_policy = {"PUNT_CREATED": True}
        retry_limit = 5
        retry_backoff_seconds = 60
        config_fingerprint = "fingerprint"
        created_at = None
        published_at = None
        tested_at = None
        supersedes_id = None

    result = smtp_config_response(Row())
    assert result["has_password"] is True
    assert "encrypted_password" not in result
    assert "encrypted-secret" not in json.dumps(result)
