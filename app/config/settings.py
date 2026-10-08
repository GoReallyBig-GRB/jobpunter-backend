from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "JobPunter API"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "postgresql+psycopg://jobpunter:jobpunter@localhost:5432/jobpunter"
    request_id_header: str = "X-Request-ID"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
