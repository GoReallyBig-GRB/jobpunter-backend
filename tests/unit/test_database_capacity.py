import pytest
from pydantic import ValidationError

from app.application.operations.database_capacity import connection_alert_level
from app.config.settings import Settings


@pytest.mark.parametrize(
    "in_use,maximum,level",
    [(0, 100, "OK"), (79, 100, "OK"), (80, 100, "WARNING"), (94, 100, "WARNING"),
     (95, 100, "CRITICAL"), (100, 100, "CRITICAL"), (1, 0, "UNKNOWN")],
)
def test_connection_alert_levels(in_use, maximum, level):
    assert connection_alert_level(in_use, maximum) == level


def test_size_thresholds_must_be_ordered():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_size_warning_bytes=10, database_size_critical_bytes=10)
    ok = Settings(_env_file=None, database_size_warning_bytes=10, database_size_critical_bytes=11)
    assert ok.database_size_warning_bytes == 10


def test_size_thresholds_default_to_not_configured():
    settings = Settings(_env_file=None)
    assert settings.database_size_warning_bytes is None
    assert settings.database_size_critical_bytes is None
