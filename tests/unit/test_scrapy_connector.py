import json
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry
from app.infrastructure.acquisition.scrapy import ScrapyConnector


def test_scrapy_connector_is_registered():
    registry = AcquisitionConnectorRegistry()
    assert ("SCRAPY", "1.0.0") in registry.supported_connectors
    assert isinstance(registry.create("scrapy", "1.0.0"), ScrapyConnector)


def test_scrapy_rejects_missing_urls_before_worker_use():
    with pytest.raises(ValueError, match="start_urls"):
        ScrapyConnector().acquire(source_id=uuid4(), context={})


def test_scrapy_rejects_non_http_urls():
    with pytest.raises(ValueError, match="absolute HTTP"):
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["file:///etc/passwd"]},
        )


def test_scrapy_rejects_unbounded_page_count():
    with pytest.raises(ValueError, match="max_pages"):
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://example.test/jobs"], "max_pages": 10000},
        )


def test_scrapy_rejects_invalid_timeout():
    with pytest.raises(ValueError, match="timeout_seconds"):
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://example.test/jobs"], "timeout_seconds": 1},
        )


def test_each_acquisition_uses_a_fresh_worker_process(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"records":[]}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    connector = ScrapyConnector()
    context = {"start_urls": ["https://example.test/jobs"]}

    assert connector.acquire(source_id=uuid4(), context=context) == []
    assert connector.acquire(source_id=uuid4(), context=context) == []

    assert len(calls) == 2
    assert calls[0][0] == calls[1][0]
    assert calls[0][0][-1] == "app.infrastructure.acquisition.scrapy_worker"
    assert calls[0][1]["timeout"] == 180
    assert calls[1][1]["timeout"] == 180
    assert json.loads(calls[0][1]["input"])["start_urls"] == context["start_urls"]


def test_scrapy_worker_records_are_normalized(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"records": [{"title": "Product Manager", "job_url": "https://example.test/job/1"}]}),
            stderr="",
        ),
    )
    source_id = uuid4()
    records = ScrapyConnector().acquire(
        source_id=source_id,
        context={"start_urls": ["https://example.test/jobs"]},
    )

    assert len(records) == 1
    assert records[0].source_id == source_id
    assert records[0].source_url == "https://example.test/job/1"
    assert records[0].raw_content["connector"] == "SCRAPY"
    assert records[0].raw_content["connector_version"] == "1.0.0"
    assert len(records[0].content_hash) == 64


def test_scrapy_timeout_becomes_safe_source_error(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="scrapy-worker", timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="timed out"):
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://example.test/jobs"]},
        )


def test_scrapy_nonzero_exit_does_not_expose_worker_output(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="secret token and operator URL",
        ),
    )
    with pytest.raises(RuntimeError, match="status 2") as exc:
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://example.test/jobs"]},
        )
    assert "secret token" not in str(exc.value)
    assert "operator URL" not in str(exc.value)


def test_scrapy_rejects_malformed_worker_output(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="not-json", stderr=""),
    )
    with pytest.raises(RuntimeError, match="invalid result payload"):
        ScrapyConnector().acquire(
            source_id=uuid4(),
            context={"start_urls": ["https://example.test/jobs"]},
        )
