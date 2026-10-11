import pytest
from pydantic import ValidationError

from app.config.settings import Settings


def test_runtime_environment_uses_canonical_jobpunter_variable():
    settings = Settings(
        _env_file=None,
        JOBPUNTER_ENVIRONMENT="production",
        APP_ENV="development",
    )
    assert settings.app_env == "production"


def test_runtime_environment_supports_legacy_app_env_alias():
    settings = Settings(_env_file=None, APP_ENV="staging")
    assert settings.app_env == "staging"


def test_development_identity_is_rejected_outside_development():
    with pytest.raises(ValidationError, match="only permitted"):
        Settings(
            _env_file=None,
            JOBPUNTER_ENVIRONMENT="production",
            JOBPUNTER_ALLOW_DEV_IDENTITY=True,
        )


def test_development_identity_can_be_explicitly_enabled_in_development():
    settings = Settings(
        _env_file=None,
        JOBPUNTER_ENVIRONMENT="development",
        JOBPUNTER_ALLOW_DEV_IDENTITY=True,
    )
    assert settings.jobpunter_allow_dev_identity is True


@pytest.mark.parametrize("value", [0, 1, 29, 3601])
def test_signed_url_ttl_rejects_unsafe_or_unreasonable_values(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, STORAGE_DOWNLOAD_URL_TTL_SECONDS=value)


@pytest.mark.parametrize("value", [0, -1, 3651])
def test_document_retention_days_must_be_positive_and_bounded(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, DOCUMENT_RETENTION_DAYS=value)
