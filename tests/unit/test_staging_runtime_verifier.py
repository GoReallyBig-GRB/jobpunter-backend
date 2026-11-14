from datetime import UTC, datetime, timedelta
from io import BytesIO
from urllib.error import HTTPError

import pytest

from scripts import verify_staging_runtime as verifier


@pytest.mark.parametrize("age_seconds,expected", [
    (0, "PASS"), (60, "PASS"), (3601, "FAIL"), (-60, "FAIL"),
    (None, "FAIL"), ("invalid", "FAIL"), ("naive", "FAIL"),
])
def test_verifier_requires_exact_revision_and_healthy_requested_surfaces(
    monkeypatch, age_seconds, expected,
):
    observed_at = (
        (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat()
        if isinstance(age_seconds, int) else
        "2026-11-04T00:00:00" if age_seconds == "naive" else age_seconds
    )
    responses = {
        "/api/v1/health": (200, {"data": {"status": "ok", "deployment_revision": "abc1234"}}),
        "/api/v1/readiness": (200, {"data": {
            "status": "ready",
            "environment": "staging",
            "deployment_revision": "abc1234",
            "checks": {
                "database": True,
                "schema_current": True,
                "deployment_revision_configured": True,
            },
            "schema": {"database_revisions": ["0081"], "code_heads": ["0081"]},
        }}),
        "/api/v1/admin/operations/summary": (200, {"data": {
            "stale_running_acquisition_runs": 0,
            "expired_processing_outbox_events": 0,
            "workers": {"stale_count": 0},
        }}),
        "/api/v1/admin/operations/workers": (200, {"data": [
            {"worker_name": "outbox", "status": "RUNNING"},
            {"worker_name": "discovery_scheduler", "status": "IDLE"},
        ]}),
        "/api/v1/admin/integrations/status": (200, {"data": {"integrations": [
            {
                "key": "smtp_email",
                "configured": True,
                "observed_status": "HEALTHY",
                "last_tested_at": observed_at,
                "last_error_category": None,
            }
        ]}}),
    }

    def fake_request(base_url, path, **kwargs):
        assert kwargs.get("token") != ""
        return responses[path]

    monkeypatch.setattr(verifier, "_request_json", fake_request)
    report = verifier.verify_runtime(
        base_url="https://staging.example.test",
        expected_revision="abc1234",
        admin_token="secret-not-reported",
        expected_workers={"outbox", "discovery_scheduler"},
        required_integrations={"smtp_email"},
        stale_after_seconds=120,
        timeout=5,
    )

    assert report["status"] == expected
    integration_check = next(c for c in report["checks"] if c["name"] == "integration_observations")
    assert integration_check["status"] == expected
    assert "secret-not-reported" not in str(report)


def test_verifier_fails_on_revision_mismatch_and_missing_admin_token(monkeypatch):
    def fake_request(base_url, path, **kwargs):
        if path.endswith("health"):
            return 200, {"data": {"status": "ok", "deployment_revision": "old1234"}}
        return 200, {"data": {
            "status": "ready",
            "environment": "staging",
            "deployment_revision": "old1234",
            "checks": {
                "database": True,
                "schema_current": True,
                "deployment_revision_configured": True,
            },
        }}

    monkeypatch.setattr(verifier, "_request_json", fake_request)
    report = verifier.verify_runtime(
        base_url="https://staging.example.test",
        expected_revision="new1234",
        admin_token=None,
        expected_workers={"outbox"},
        required_integrations=set(),
        stale_after_seconds=120,
        timeout=5,
    )

    assert report["status"] == "FAIL"
    assert {check["name"] for check in report["checks"] if check["status"] == "FAIL"} == {
        "readiness", "admin_evidence",
    }


def test_observation_age_boundary():
    now = datetime(2026, 11, 14, tzinfo=UTC)
    assert verifier._observation_is_fresh(
        (now - timedelta(seconds=3600)).isoformat(), now=now, max_age_seconds=3600,
    )
    assert not verifier._observation_is_fresh(
        (now - timedelta(seconds=3601)).isoformat(), now=now, max_age_seconds=3600,
    )


@pytest.mark.parametrize("http_error", [False, True])
@pytest.mark.parametrize("oversized", [False, True])
def test_response_body_is_bounded_and_closed(monkeypatch, http_error, oversized):
    body = b'{"ok":true}'
    if oversized:
        body = b" " * (verifier.MAX_RESPONSE_BYTES + 1)

    class Response(BytesIO):
        status = 200

        def read(self, size=-1):
            assert size == verifier.MAX_RESPONSE_BYTES + 1
            return super().read(size)

    response = Response(body)

    def open_response(request, timeout):
        assert timeout == 5
        if http_error:
            raise HTTPError(request.full_url, 503, "Unavailable", {}, response)
        return response

    monkeypatch.setattr(verifier, "urlopen", open_response)
    status, payload = verifier._request_json("https://staging.example.test", "/test", timeout=5)
    assert status == (503 if http_error else 200)
    assert payload == ({"response_error": "RESPONSE_TOO_LARGE"} if oversized else {"ok": True})
    assert response.closed


@pytest.mark.parametrize("timeout", [0, -1, 61, float("nan"), float("inf")])
def test_request_rejects_unbounded_timeout_before_network(monkeypatch, timeout):
    def unexpected_request(*args, **kwargs):
        pytest.fail("invalid timeout must not reach network")

    monkeypatch.setattr(verifier, "urlopen", unexpected_request)
    with pytest.raises(ValueError, match="timeout must"):
        verifier._request_json("https://staging.example.test", "/test", timeout=timeout)


@pytest.mark.parametrize("arguments", [
    ["--timeout", "nan"], ["--timeout", "0"],
    ["--integration-max-age-seconds", "0"],
    ["--integration-max-age-seconds", "86401"],
])
def test_cli_rejects_invalid_bounds(arguments):
    with pytest.raises(SystemExit):
        verifier.main(["--base-url", "https://staging.example.test",
                       "--expected-revision", "abc1234", *arguments])


def test_error_body_read_failure_is_sanitized_and_closed(monkeypatch):
    class BrokenResponse(BytesIO):
        def read(self, size=-1):
            raise OSError("private upstream message")

    response = BrokenResponse()

    def open_response(request, timeout):
        raise HTTPError(request.full_url, 503, "Unavailable", {}, response)

    monkeypatch.setattr(verifier, "urlopen", open_response)
    status, payload = verifier._request_json("https://staging.example.test", "/test")
    assert status == 0
    assert payload == {"transport_error": "OSError"}
    assert response.closed
