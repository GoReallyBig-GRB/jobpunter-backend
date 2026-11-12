import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine

from app.config.settings import Settings
from app.infrastructure.db.session import engine_options


def _settings(**env):
    return Settings(_env_file=None, **env)


def test_defaults_match_sqlalchemy_defaults():
    options = engine_options(_settings())
    assert options == {
        "pool_pre_ping": True,
        "pool_size": 5,
        "max_overflow": 10,
        "pool_recycle": -1,
    }


def test_environment_overrides_reach_engine_options(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "2")
    monkeypatch.setenv("DB_POOL_RECYCLE_SECONDS", "300")
    options = engine_options(Settings(_env_file=None))
    assert (options["pool_size"], options["max_overflow"], options["pool_recycle"]) == (3, 2, 300)


def test_engine_is_built_with_the_configured_pool():
    options = engine_options(_settings(db_pool_size=2, db_max_overflow=1, db_pool_recycle_seconds=60))
    engine = create_engine("postgresql+psycopg://u:p@localhost:5432/db", **options)
    try:
        assert engine.pool.size() == 2
        assert engine.pool._max_overflow == 1
        assert engine.pool._recycle == 60
        assert engine.pool._pre_ping is True
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "field,value",
    [
        ("db_pool_size", 0),
        ("db_pool_size", 51),
        ("db_max_overflow", -1),
        ("db_max_overflow", 51),
        ("db_pool_recycle_seconds", -2),
        ("db_pool_recycle_seconds", 86401),
    ],
)
def test_out_of_range_pool_values_are_rejected(field, value):
    with pytest.raises(ValidationError):
        _settings(**{field: value})
