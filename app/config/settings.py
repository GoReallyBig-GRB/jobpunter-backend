from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://jobpunter:jobpunter@localhost:5432/jobpunter"
    # R2 is the authoritative document store. GCS is retained only as an explicit migration option.
    document_storage_provider: str = "r2"
    r2_bucket: str | None = None
    r2_endpoint_url: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    google_cloud_storage_bucket: str | None = None
    storage_download_url_ttl_seconds: int = 300
    document_upload_max_bytes: int = 4 * 1024 * 1024
    document_retention_days: int = 30
    google_drive_backup_enabled: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
