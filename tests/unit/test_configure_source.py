from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import pytest

from app.infrastructure.db.models import Base, Source, SourceConnector
from app.workers.configure_source import configure_source


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def valid_config():
    return {
        "source": {
            "name": "Example jobs",
            "source_type": "JOB_BOARD",
            "base_url": "https://example.test",
        },
        "connector": {
            "connector_type": "JOBSPY",
            "connector_version": "1.0.0",
        },
    }


def test_registers_source_and_connector_idempotently(db):
    config = valid_config()
    first = configure_source(db, config)
    second = configure_source(db, config)

    assert first == second
    assert len(list(db.scalars(select(Source)))) == 1
    assert len(list(db.scalars(select(SourceConnector)))) == 1


def test_rejects_unknown_connector_before_database_changes(db):
    config = valid_config()
    config["connector"]["connector_type"] = "UNKNOWN"

    with pytest.raises(ValueError, match="unsupported connector"):
        configure_source(db, config)

    assert list(db.scalars(select(Source))) == []
    assert list(db.scalars(select(SourceConnector))) == []


def test_rejects_non_object_configuration(db):
    with pytest.raises(ValueError, match="source must be a JSON object"):
        configure_source(db, {"source": [], "connector": {}})
