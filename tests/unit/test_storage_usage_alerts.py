import pytest
from pydantic import ValidationError

from app.application.documents.usage import storage_alert_level
from app.config.settings import Settings


@pytest.mark.parametrize("used,warn,crit,expected", [
    (10, None, None, "NOT_CONFIGURED"),
    (10, 100, None, "OK"),
    (100, 100, None, "WARNING"),
    (99, 100, 200, "OK"),
    (150, 100, 200, "WARNING"),
    (200, 100, 200, "CRITICAL"),
    (50, None, 40, "CRITICAL"),
])
def test_alert_level(used, warn, crit, expected):
    assert storage_alert_level(used, warn, crit) == expected


def test_settings_reject_inverted_or_non_positive_thresholds():
    with pytest.raises(ValidationError):
        Settings(storage_usage_warning_bytes=200, storage_usage_critical_bytes=100)
    with pytest.raises(ValidationError):
        Settings(storage_usage_warning_bytes=0)
