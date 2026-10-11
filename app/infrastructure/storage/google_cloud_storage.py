"""Google Cloud Storage adapter for private object storage.

The application depends on the ObjectStorage port, not on this provider.
Objects are created with a no-overwrite precondition and are never made public.
"""
from datetime import timedelta
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


class GoogleCloudStorageAdapter:
    def __init__(self, bucket_name: str, client=None):
        if not isinstance(bucket_name, str) or not bucket_name.strip():
            raise ValueError("bucket_name is required")
        if client is None:
            from google.cloud import storage

            client = storage.Client()
        self._bucket = client.bucket(bucket_name)

    def put(self, storage_key: str, content: bytes, content_type: str) -> StoredObject:
        _validate_key(storage_key)
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        if not isinstance(content_type, str) or not content_type.strip():
            raise ValueError("content_type is required")
        blob = self._bucket.blob(storage_key)
        blob.upload_from_string(content, content_type=content_type, if_generation_match=0)
        return StoredObject(
            storage_key=storage_key,
            checksum_sha256=sha256(content).hexdigest(),
            byte_size=len(content),
        )

    def get(self, storage_key: str) -> bytes:
        _validate_key(storage_key)
        return self._bucket.blob(storage_key).download_as_bytes()

    def delete(self, storage_key: str) -> None:
        _validate_key(storage_key)
        self._bucket.blob(storage_key).delete()

    def create_download_url(self, storage_key: str, expires_in_seconds: int) -> str:
        _validate_key(storage_key)
        if (
            not isinstance(expires_in_seconds, int)
            or isinstance(expires_in_seconds, bool)
            or not 1 <= expires_in_seconds <= 3600
        ):
            raise ValueError("expires_in_seconds must be between 1 and 3600")
        return self._bucket.blob(storage_key).generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=expires_in_seconds),
            method="GET",
        )
