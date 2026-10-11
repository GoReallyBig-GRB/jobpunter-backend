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
    jobpunter_allow_dev_identity: bool = False
    database_url: str = "postgresql+psycopg://jobpunter:jobpunter@localhost:5432/jobpunter"
    # R2 is the authoritative document store. GCS is retained only as an explicit migration option.
    document_storage_provider: str = "r2"
    r2_bucket: str | None = None
    r2_endpoint_url: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    google_cloud_storage_bucket: str | None = None
    storage_download_url_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    document_retention_days: int = Field(default=30, ge=1, le=3650)
    google_drive_backup_enabled: bool = False
    google_drive_client_id: str | None = None
    google_drive_client_secret: str | None = None
    google_drive_refresh_token: str | None = None
    google_drive_folder_id: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_development_identity(self) -> "Settings":
        if self.jobpunter_allow_dev_identity and self.app_env.strip().lower() != "development":
            raise ValueError(
                "JOBPUNTER_ALLOW_DEV_IDENTITY=true is only permitted when "
                "JOBPUNTER_ENVIRONMENT=development"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
