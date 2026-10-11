"""Construct the configured private object-storage provider."""
from app.api.errors import DomainError
from app.config.settings import Settings
from app.infrastructure.storage.cloudflare_r2 import CloudflareR2StorageAdapter
from app.infrastructure.storage.google_cloud_storage import GoogleCloudStorageAdapter
from app.ports.object_storage import ObjectStorage


def build_object_storage(settings: Settings) -> ObjectStorage:
    provider = settings.document_storage_provider.strip().lower()
    if provider == "r2":
        required = (
            settings.r2_bucket,
            settings.r2_endpoint_url,
            settings.r2_access_key_id,
            settings.r2_secret_access_key,
        )
        if not all(isinstance(value, str) and value.strip() for value in required):
            raise DomainError(
                "OBJECT_STORAGE_NOT_CONFIGURED",
                "Cloudflare R2 document storage is not configured.",
                503,
            )
        return CloudflareR2StorageAdapter(
            bucket_name=settings.r2_bucket,
            endpoint_url=settings.r2_endpoint_url,
            access_key_id=settings.r2_access_key_id,
            secret_access_key=settings.r2_secret_access_key,
        )
    if provider == "gcs":
        if not settings.google_cloud_storage_bucket:
            raise DomainError(
                "OBJECT_STORAGE_NOT_CONFIGURED",
                "Google Cloud Storage is not configured.",
                503,
            )
        return GoogleCloudStorageAdapter(settings.google_cloud_storage_bucket)
    raise DomainError(
        "OBJECT_STORAGE_PROVIDER_INVALID",
        "Configured document storage provider is unsupported.",
        503,
    )
