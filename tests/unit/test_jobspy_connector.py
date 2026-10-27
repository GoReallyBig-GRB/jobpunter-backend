from datetime import datetime, timezone
import sys
import types
from uuid import uuid4

import pytest

from app.infrastructure.acquisition.jobspy import JobSpyConnector
from app.infrastructure.acquisition import jobspy_transport
from app.ports.acquisition import AcquisitionAdapterError, AcquisitionOutcome


class FakeFrame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, *, orient):
        assert orient == "records"
        return self.rows


def install_fake_jobspy(monkeypatch, scrape_jobs):
    package = types.ModuleType("jobspy")
    package.__path__ = []
    package.scrape_jobs = scrape_jobs
    util = types.ModuleType("jobspy.util")

    class FakeRequestsRotating:
        def __init__(self, *args, **kwargs):
            self.trust_env = True
            self.mounts = {}

        def mount(self, prefix, adapter):
            self.mounts[prefix] = adapter

    class FakeTLSRotating:
        pass

    util.RequestsRotating = FakeRequestsRotating
    util.TLSRotating = FakeTLSRotating
    monkeypatch.setitem(sys.modules, "jobspy", package)
    monkeypatch.setitem(sys.modules, "jobspy.util", util)


def test_jobspy_connector_normalizes_results_and_hashes_content(monkeypatch):
    calls = []

    def fake_scrape_jobs(**kwargs):
        calls.append(kwargs)
        return FakeFrame([
            {"id": "abc", "title": "Product Manager", "company": "Example",
             "job_url": "https://example.com/jobs/abc", "date_posted": None},
        ])

    install_fake_jobspy(monkeypatch, fake_scrape_jobs)
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
    def fake_scrape_jobs(**kwargs):
        if kwargs["search_term"] == "Second term":
            raise RuntimeError("provider response must not be exposed")
        return FakeFrame([{
            "id": "first",
            "title": "Product Manager",
            "job_url": "https://example.com/jobs/first",
        }])

    install_fake_jobspy(monkeypatch, fake_scrape_jobs)
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
    rows = [
        {"id": str(index), "title": "Product Manager", "job_url": f"https://example.com/jobs/{index}"}
        for index in range(501)
    ]
    install_fake_jobspy(monkeypatch, lambda **_kwargs: FakeFrame(rows))

    result = JobSpyConnector().acquire(
        source_id=uuid4(),
        context={"search_terms": ["PM"], "location": "Nigeria", "results_wanted": 100},
    )

    assert result.status == AcquisitionOutcome.PARTIAL
    assert result.error_category == "MAX_ITEMS_LIMIT_REACHED"
    assert len(result.records) == JobSpyConnector.MAX_ITEMS


def test_jobspy_uses_live_policy_scope_without_disabling_acquisition(monkeypatch):
    calls = []

    def fake_scrape_jobs(**kwargs):
        calls.append(kwargs)
        return FakeFrame([{"id": "ok", "title": "Product Manager"}])

    install_fake_jobspy(monkeypatch, fake_scrape_jobs)
    result = JobSpyConnector().acquire(
        source_id=uuid4(),
        context={
            "search_terms": ["Product Manager"],
            "location": "Nigeria",
            "acquisition_policy_revalidator": lambda **kwargs: None,
        },
    )

    assert result.status == AcquisitionOutcome.SUCCEEDED
    assert len(result.records) == 1
    assert calls[0]["search_term"] == "Product Manager"


def test_jobspy_transport_revalidates_policy_before_http_request(monkeypatch):
    import ipaddress

    monkeypatch.setattr(
        jobspy_transport, "_resolve_public",
        lambda _host, _port: ipaddress.ip_address("93.184.216.34"),
    )
    observed = []

    def deny(**kwargs):
        observed.append(kwargs)
        raise AcquisitionAdapterError(AcquisitionOutcome.RESTRICTED, "SOURCE_POLICY_CHANGED")

    request = types.SimpleNamespace(url="https://jobs.example/search", method="GET")
    adapter = jobspy_transport._GuardedHTTPAdapter()
    with jobspy_transport.guarded_jobspy_policy({"acquisition_policy_revalidator": deny}):
        with pytest.raises(AcquisitionAdapterError, match="SOURCE_POLICY_CHANGED"):
            adapter.send(request)

    assert len(observed) == 1
    assert observed[0]["url"] == "https://jobs.example/search"
    assert observed[0]["method"] == "GET"


def test_jobspy_transport_rejects_unsupported_method_before_network(monkeypatch):
    import ipaddress

    monkeypatch.setattr(
        jobspy_transport, "_resolve_public",
        lambda _host, _port: ipaddress.ip_address("93.184.216.34"),
    )
    request = types.SimpleNamespace(url="https://jobs.example/search", method="PUT")
    adapter = jobspy_transport._GuardedHTTPAdapter()
    with jobspy_transport.guarded_jobspy_policy({}):
        with pytest.raises(AcquisitionAdapterError, match="ACQUISITION_METHOD_NOT_ALLOWED"):
            adapter.send(request)


def test_jobspy_transport_allows_post_when_policy_permits(monkeypatch):
    import ipaddress

    monkeypatch.setattr(
        jobspy_transport, "_resolve_public",
        lambda _host, _port: ipaddress.ip_address("93.184.216.34"),
    )
    observed = []
    monkeypatch.setattr(
        jobspy_transport, "assert_current_target_not_forbidden",
        lambda **kwargs: observed.append(kwargs),
    )
    with jobspy_transport.guarded_jobspy_policy({}):
        host, port, address = jobspy_transport._validate(
            "https://apis.indeed.com/graphql", "POST"
        )

    assert (host, port, address) == ("apis.indeed.com", 443, "93.184.216.34")
    assert len(observed) == 1
    assert observed[0]["method"] == "POST"


def test_jobspy_tls_transport_applies_resolve_option_at_session_level(monkeypatch):
    import ipaddress

    class FakeCurlOpt:
        RESOLVE = "resolve"

    class FakeResponse:
        status_code = 200
        headers = {}

    class FakeTLSRotating:
        def __init__(self, *args, **kwargs):
            self.curl_options = {"existing": "preserved"}
            self.captured_options = None

        def request(self, method, url, *args, **kwargs):
            assert "curl_options" not in kwargs
            self.captured_options = dict(self.curl_options)
            return FakeResponse()

    fake_curl_cffi = types.ModuleType("curl_cffi")
    fake_curl_cffi.CurlOpt = FakeCurlOpt
    monkeypatch.setitem(sys.modules, "curl_cffi", fake_curl_cffi)
    monkeypatch.setattr(
        jobspy_transport, "_resolve_public",
        lambda _host, _port: ipaddress.ip_address("93.184.216.34"),
    )
    monkeypatch.setattr(
        jobspy_transport, "assert_current_target_not_forbidden",
        lambda **_kwargs: None,
    )
    install_fake_jobspy(monkeypatch, lambda **_kwargs: FakeFrame([]))
    util = sys.modules["jobspy.util"]
    util.TLSRotating = FakeTLSRotating
    jobspy_transport.install_jobspy_transport()
    session = util.TLSRotating()

    with jobspy_transport.guarded_jobspy_policy({}):
        response = session.request("GET", "https://jobs.example/search")

    assert response.status_code == 200
    assert session.captured_options == {
        "existing": "preserved",
        "resolve": ["jobs.example:443:93.184.216.34"],
    }
    assert session.curl_options == {"existing": "preserved"}


def test_jobspy_transport_connects_to_pinned_ip(monkeypatch):
    connected = []
    monkeypatch.setattr(
        jobspy_transport.connection_util,
        "create_connection",
        lambda address, *_args, **_kwargs: connected.append(address) or object(),
    )
    pinned_connection = jobspy_transport._PinnedHTTPConnection("jobs.example", 443)
    previous = getattr(jobspy_transport._PINNED, "addresses", {})
    jobspy_transport._PINNED.addresses = {("jobs.example", 443): "93.184.216.34"}
    try:
        pinned_connection._new_conn()
    finally:
        jobspy_transport._PINNED.addresses = previous

    assert connected == [("93.184.216.34", 443)]

def test_jobspy_https_transport_connects_to_pinned_ip(monkeypatch):
    connected = []
    monkeypatch.setattr(
        jobspy_transport.connection_util,
        "create_connection",
        lambda address, *_args, **_kwargs: connected.append(address) or object(),
    )
    pinned_connection = jobspy_transport._PinnedHTTPSConnection(
        "jobs.example", 443
    )
    previous = getattr(jobspy_transport._PINNED, "addresses", {})
    jobspy_transport._PINNED.addresses = {
        ("jobs.example", 443): "93.184.216.34"
    }
    try:
        pinned_connection._new_conn()
    finally:
        jobspy_transport._PINNED.addresses = previous

    assert connected == [("93.184.216.34", 443)]
