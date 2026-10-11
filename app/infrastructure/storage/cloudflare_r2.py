"""Cloudflare R2 adapter using its S3-compatible private-object API."""
from hashlib import sha256
from pathlib import PurePosixPath

from app.ports.object_storage import StoredObject


def _validate_key(storage_key: str) -> None:
    if (
        not isinstance(storage_key, str)
        or not storage_key.strip()
        or storage_key.startswith("/")
        or "\\" in storage_key
        or any(part in {"", ".", ".."} for part in PurePosixPath(storage_key).parts)
    ):
        raise ValueError("storage_key must be a normalized private object key")


class CloudflareR2StorageAdapter:
    """Stores private objects in R2; never changes bucket or object public-access policy."""

    def __init__(
        self,
        bucket_name: str,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        client=None,
    ):
        for name, value in (
            ("bucket_name", bucket_name),
            ("endpoint_url", endpoint_url),
            ("access_key_id", access_key_id),
            ("secret_access_key", secret_access_key),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if client is None:
            import boto3

            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url.rstrip("/"),
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                region_name="auto",
            )
        self._client = client
        self._bucket_name = bucket_name

    def put(self, storage_key: str, content: bytes, content_type: str) -> StoredObject:
        _validate_key(storage_key)
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        if not isinstance(content_type, str) or not content_type.strip():
            raise ValueError("content_type is required")
        checksum = sha256(content).hexdigest()
        self._client.put_object(
            Bucket=self._bucket_name,
            Key=storage_key,
            Body=content,
            ContentType=content_type,
            Metadata={"sha256": checksum},
            IfNoneMatch="*",
        )
        return StoredObject(storage_key=storage_key, checksum_sha256=checksum, byte_size=len(content))

    def get(self, storage_key: str) -> bytes:
        _validate_key(storage_key)
        response = self._client.get_object(Bucket=self._bucket_name, Key=storage_key)
        body = response["Body"]
        try:
            return body.read()
        finally:
            close = getattr(body, "close", None)
            if close:
                close()

    def delete(self, storage_key: str) -> None:
        _validate_key(storage_key)
        self._client.delete_object(Bucket=self._bucket_name, Key=storage_key)

    def create_download_url(self, storage_key: str, expires_in_seconds: int) -> str:
        _validate_key(storage_key)
        if (
            not isinstance(expires_in_seconds, int)
            or isinstance(expires_in_seconds, bool)
            or not 1 <= expires_in_seconds <= 3600
        ):
            raise ValueError("expires_in_seconds must be between 1 and 3600")
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket_name, "Key": storage_key},
            ExpiresIn=expires_in_seconds,
        )
