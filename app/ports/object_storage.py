"""Provider-neutral contract for private object storage."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class StoredObject:
    storage_key: str
    checksum_sha256: str
    byte_size: int


class ObjectStorage(Protocol):
    """Private binary storage contract; implementations must not make objects public."""

    def put(self, storage_key: str, content: bytes, content_type: str) -> StoredObject:
        """Create an object without overwriting an existing key."""
        ...

    def get(self, storage_key: str) -> bytes:
        """Read the exact bytes stored at a key."""
        ...

    def delete(self, storage_key: str) -> None:
        """Delete an object when compensating for a failed metadata transaction."""
        ...

    def create_download_url(self, storage_key: str, expires_in_seconds: int) -> str:
        """Create a private, time-limited download URL."""
        ...
