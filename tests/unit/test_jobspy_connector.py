from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.infrastructure.acquisition.jobspy import JobSpyConnector
from app.ports.acquisition import AcquisitionOutcome


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

    assert results.status == AcquisitionOutcome.SUCCEEDED
    assert len(results.records) == 1
    item = results.records[0]
    assert item.source_id == source_id
    assert item.external_record_id == "abc"
    assert item.source_url == "https://example.com/jobs/abc"
    assert item.raw_content["query"] == "Technical Product Manager"
    assert item.content_hash == JobSpyConnector().acquire(
        source_id=source_id,
        context={"search_terms": ["Technical Product Manager"], "location": "Nigeria"},
    ).records[0].content_hash
    assert calls[0]["site_name"] == ["indeed", "google"]
    assert item.observed_at.tzinfo == timezone.utc


@pytest.mark.parametrize(
    "context, message",
    [
        ({"location": "Nigeria"}, "search_terms"),
        ({"search_terms": ["PM"]}, "location"),
        ({"search_terms": ["PM"], "location": "Nigeria", "results_wanted": 101}, "results_wanted"),
        ({"search_terms": ["PM"], "location": "Nigeria", "site_names": ["unknown"]}, "Unsupported"),
        ({"search_terms": ["PM"] * 21, "location": "Nigeria"}, "at most 20 terms"),
    ],
)
def test_jobspy_connector_rejects_invalid_configuration(context, message):
    with pytest.raises(ValueError, match=message):
        JobSpyConnector().acquire(source_id=uuid4(), context=context)



def test_jobspy_preserves_records_when_a_later_search_term_fails(monkeypatch):
    import sys

    def fake_scrape_jobs(**kwargs):
        if kwargs["search_term"] == "Second term":
            raise RuntimeError("provider response must not be exposed")
        return FakeFrame([{
            "id": "first",
            "title": "Product Manager",
            "job_url": "https://example.com/jobs/first",
        }])

    monkeypatch.setitem(sys.modules, "jobspy", SimpleNamespace(scrape_jobs=fake_scrape_jobs))
    result = JobSpyConnector().acquire(
        source_id=uuid4(),
        context={
            "search_terms": ["First term", "Second term"],
            "location": "Nigeria",
        },
    )

    assert result.status == AcquisitionOutcome.PARTIAL
    assert len(result.records) == 1
    assert result.error_category == "JOBSPY_QUERY_FAILED"
    assert "provider response" not in str(result)



def test_jobspy_caps_total_acquired_records(monkeypatch):
    import sys

    rows = [
        {"id": str(index), "title": "Product Manager", "job_url": f"https://example.com/jobs/{index}"}
        for index in range(501)
    ]
    monkeypatch.setitem(
        sys.modules,
        "jobspy",
        SimpleNamespace(scrape_jobs=lambda **_kwargs: FakeFrame(rows)),
    )

    result = JobSpyConnector().acquire(
        source_id=uuid4(),
        context={"search_terms": ["PM"], "location": "Nigeria", "results_wanted": 100},
    )

    assert result.status == AcquisitionOutcome.PARTIAL
    assert result.error_category == "MAX_ITEMS_LIMIT_REACHED"
    assert len(result.records) == JobSpyConnector.MAX_ITEMS
