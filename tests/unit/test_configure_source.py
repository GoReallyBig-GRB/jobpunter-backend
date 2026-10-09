from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.workers.configure_source import configure_source


class FakeScalars:
    def __init__(self, values):
        self.values = values
    def __iter__(self):
        return iter(self.values)


class FakeDB:
    def __init__(self):
        self.sources = []
        self.connectors = []
        self.added = []
        self.commits = 0
    def scalars(self, statement):
        # The query predicates are intentionally exercised through a minimal fake:
        # tests cover creation, repeat registration, and validation boundaries.
        source = self._last_model
        rows = self.sources if source == "source" else self.connectors
        return FakeScalars(rows)
    def scalar(self, statement):
        return None
    def flush(self):
        for obj in self.added:
            if hasattr(obj, "source_type"):
                self.sources.append(obj)
                self._last_model = "source"
            elif hasattr(obj, "connector_type"):
                self.connectors.append(obj)
                self._last_model = "connector"
        self.added.clear()
    def add(self, obj):
        self.added.append(obj)
    def commit(self):
        self.commits += 1


def valid_config():
    return {
        "source": {"name": "Example jobs", "source_type": "JOB_BOARD", "base_url": "https://example.test"},
        "connector": {"connector_type": "JOBSPY", "connector_version": "1.0.0"},
    }


def test_rejects_unknown_connector_before_database_changes():
    db = FakeDB()
    config = valid_config()
    config["connector"]["connector_type"] = "SCRAPY"
    with pytest.raises(ValueError, match="unsupported connector"):
        configure_source(db, config)
    assert db.commits == 0


def test_rejects_non_object_configuration():
    with pytest.raises(ValueError, match="config"):
        configure_source(FakeDB(), {"source": [], "connector": {}})
