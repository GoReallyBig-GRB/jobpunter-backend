from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.workers.discovery_mission_worker import (
    WAVE_CONNECTORS,
    _source_matches_filters,
    run_worker,
)


def test_wave_connector_mapping_uses_registered_acquisition_families():
    assert WAVE_CONNECTORS["jobspy"] == {"JOBSPY"}
    assert WAVE_CONNECTORS["targeted_scrapers"] == {"SCRAPY"}
    assert WAVE_CONNECTORS["browser_automation"] == {"PLAYWRIGHT"}
    # Official feed types are reserved for a future connector implementation.
    assert "JOBSPY" not in WAVE_CONNECTORS["official_feeds"]


def test_source_filters_are_allow_only_and_case_insensitive():
    source_id = uuid4()
    source = SimpleNamespace(id=source_id, source_type="JOB_BOARD")
    connector = SimpleNamespace(connector_type="JOBSPY")

    assert _source_matches_filters(source, connector, {})
    assert _source_matches_filters(source, connector, {
        "source_ids": [str(source_id)],
        "source_types": ["job_board"],
        "connector_types": ["jobspy"],
    })
    assert not _source_matches_filters(source, connector, {"source_ids": [str(uuid4())]})
    assert not _source_matches_filters(source, connector, {"connector_types": ["SCRAPY"]})


def test_source_filters_reject_nonmatching_source_type():
    source = SimpleNamespace(id=uuid4(), source_type="AGENCY")
    connector = SimpleNamespace(connector_type="SCRAPY")
    assert not _source_matches_filters(source, connector, {"source_types": ["EMPLOYER"]})


def test_run_worker_once_does_not_sleep_or_loop():
    class FakeWorker:
        calls = 0

        def run_once(self, limit):
            self.calls += 1
            assert limit == 4
            return {"claimed": 0, "advanced": 0, "failed": 0}

    fake = FakeWorker()
    sleeps = []
    cycles = run_worker(
        once=True,
        batch_size=4,
        worker=fake,
        sleep=sleeps.append,
        should_stop=lambda: False,
    )
    assert cycles == 1
    assert fake.calls == 1
    assert sleeps == []


def test_run_worker_rejects_invalid_batch_or_interval():
    with pytest.raises(ValueError):
        run_worker(interval_seconds=0)
    with pytest.raises(ValueError):
        run_worker(batch_size=0)
