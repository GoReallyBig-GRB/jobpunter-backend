"""Safe handling for admin-managed SMTP credentials and configuration fingerprints."""
import hashlib
import json
import os

from cryptography.fernet import Fernet, InvalidToken


def _fernet() -> Fernet:
    key = os.getenv("JOBPUNTER_SMTP_SECRET_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError("smtp_secret_encryption_key_not_configured")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("smtp_secret_encryption_key_invalid") from exc


def encrypt_smtp_password(password: str | None) -> str | None:
    if password is None or password == "":
        return None
    return _fernet().encrypt(password.encode("utf-8")).decode("ascii")


def decrypt_smtp_password(encrypted_password: str | None) -> str | None:
    if not encrypted_password:
        return None
    try:
        return _fernet().decrypt(encrypted_password.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("smtp_secret_decryption_failed") from exc


def smtp_config_fingerprint(config: dict) -> str:
    safe = {key: value for key, value in config.items() if key not in {"password", "encrypted_password"}}
    canonical = json.dumps(safe, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def smtp_config_response(row) -> dict:
    """Never return encrypted or plaintext credential material."""
    return {
        "id": str(row.id),
        "version": row.version,
        "status": row.status,
        "provider_name": row.provider_name,
        "host": row.host,
        "port": row.port,
        "use_tls": row.use_tls,
        "username": row.username,
        "has_password": bool(row.encrypted_password),
        "from_email": row.from_email,
        "event_policy": row.event_policy,
        "retry_limit": row.retry_limit,
        "retry_backoff_seconds": row.retry_backoff_seconds,
        "config_fingerprint": row.config_fingerprint,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "supersedes_id": str(row.supersedes_id) if row.supersedes_id else None,
    }
