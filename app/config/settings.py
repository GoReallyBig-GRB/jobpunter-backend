from functools import lru_cache

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # JOBPUNTER_ENVIRONMENT is the canonical runtime switch used by auth dependencies.
    # APP_ENV remains a legacy alias for existing local deployments.
    app_env: str = Field(
        default="development",
        validation_alias=AliasChoices("JOBPUNTER_ENVIRONMENT", "APP_ENV"),
    )
    # Inject the exact deployed Git commit. Non-development readiness fails closed
    # when this is missing so staging evidence cannot be detached from source.
    deployment_revision: str | None = Field(
        default=None,
        validation_alias="JOBPUNTER_DEPLOYMENT_REVISION",
        pattern=r"^[0-9a-f]{7,40}$",
    )
    jobpunter_allow_dev_identity: bool = False
    database_url: str = "postgresql+psycopg://jobpunter:jobpunter@localhost:5432/jobpunter"
    # Per-process SQLAlchemy pool. Defaults equal SQLAlchemy's own (5 + 10 overflow, no recycle) so
    # behaviour is unchanged unless set; small values suit a pooled (PgBouncer) database host.
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=10, ge=0, le=50)
    # Seconds before a pooled connection is replaced; -1 disables recycling.
    db_pool_recycle_seconds: int = Field(default=-1, ge=-1, le=86400)
    # Optional alert thresholds for the database size reported in the admin operations summary.
    # Unset means "not configured". Neon Free caps storage at 1 GB per project.
    database_size_warning_bytes: int | None = Field(default=None, ge=1)
    database_size_critical_bytes: int | None = Field(default=None, ge=1)
    # R2 is the authoritative document store. GCS is retained only as an explicit migration option.
    document_storage_provider: str = "r2"
    r2_bucket: str | None = None
    r2_endpoint_url: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    google_cloud_storage_bucket: str | None = None
    storage_download_url_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    document_retention_days: int = Field(default=30, ge=1, le=3650)
    # Optional alert thresholds for stored document bytes (K2.4). Unset means "not configured".
    storage_usage_warning_bytes: int | None = Field(default=None, ge=1)
    storage_usage_critical_bytes: int | None = Field(default=None, ge=1)
    # Global ceiling on scheduled source refreshes being processed at once (E4 concurrency ceiling).
    source_refresh_max_concurrent: int = Field(default=4, ge=1, le=64)
    document_ocr_enabled: bool = False
    document_ocr_timeout_seconds: int = Field(default=30, ge=5, le=120)
    document_ocr_batch_size: int = Field(default=8, ge=1, le=16)
    google_drive_backup_enabled: bool = False
    google_drive_client_id: str | None = None
    google_drive_client_secret: str | None = None
    google_drive_refresh_token: str | None = None
    google_drive_folder_id: str | None = None
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_use_tls: bool = True
    smtp_timeout_seconds: int = Field(default=10, ge=1, le=60)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_development_identity(self) -> "Settings":
        if self.jobpunter_allow_dev_identity and self.app_env.strip().lower() != "development":
            raise ValueError(
                "JOBPUNTER_ALLOW_DEV_IDENTITY=true is only permitted when "
                "JOBPUNTER_ENVIRONMENT=development"
            )
        return self

    @model_validator(mode="after")
    def validate_storage_thresholds(self) -> "Settings":
        warning, critical = self.storage_usage_warning_bytes, self.storage_usage_critical_bytes
        if warning is not None and critical is not None and warning >= critical:
            raise ValueError("storage_usage_warning_bytes must be lower than storage_usage_critical_bytes")
        return self

    @model_validator(mode="after")
    def validate_database_size_thresholds(self) -> "Settings":
        warning, critical = self.database_size_warning_bytes, self.database_size_critical_bytes
        if warning is not None and critical is not None and warning >= critical:
            raise ValueError("database_size_warning_bytes must be lower than database_size_critical_bytes")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
