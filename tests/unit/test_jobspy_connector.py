from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.infrastructure.acquisition.jobspy import JobSpyConnector


class FakeFrame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, *, orient):
        assert orient == "records"
        return self.rows


def test_jobspy_connector_normalizes_results_and_hashes_content(monkeypatch):
    calls = []

    def fake_scrape_jobs(**kwargs):
        calls.append(kwargs)
        return FakeFrame([
            {"id": "abc", "title": "Product Manager", "company": "Example",
             "job_url": "https://example.com/jobs/abc", "date_posted": None},
        ])

    import sys
    monkeypatch.setitem(sys.modules, "jobspy", SimpleNamespace(scrape_jobs=fake_scrape_jobs))
    source_id = uuid4()
    results = JobSpyConnector().acquire(
        source_id=source_id,
        context={"search_terms": ["Technical Product Manager"], "location": "Nigeria"},
    )

    assert len(results) == 1
    item = results[0]
    assert item.source_id == source_id
    assert item.external_record_id == "abc"
    assert item.source_url == "https://example.com/jobs/abc"
    assert item.raw_content["query"] == "Technical Product Manager"
    assert item.content_hash == JobSpyConnector().acquire(
        source_id=source_id,
        context={"search_terms": ["Technical Product Manager"], "location": "Nigeria"},
    )[0].content_hash
    assert calls[0]["site_name"] == ["indeed", "google"]
    assert item.observed_at.tzinfo == timezone.utc


@pytest.mark.parametrize(
    "context, message",
    [
        ({"location": "Nigeria"}, "search_terms"),
        ({"search_terms": ["PM"]}, "location"),
        ({"search_terms": ["PM"], "location": "Nigeria", "results_wanted": 101}, "results_wanted"),
        ({"search_terms": ["PM"], "location": "Nigeria", "site_names": ["unknown"]}, "Unsupported"),
    ],
)
def test_jobspy_connector_rejects_invalid_configuration(context, message):
    with pytest.raises(ValueError, match=message):
        JobSpyConnector().acquire(source_id=uuid4(), context=context)
