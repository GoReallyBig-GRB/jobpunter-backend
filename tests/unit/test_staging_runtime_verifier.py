from scripts import verify_staging_runtime as verifier


def test_verifier_requires_exact_revision_and_healthy_requested_surfaces(monkeypatch):
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
                "last_tested_at": "2026-11-04T00:00:00Z",
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

    assert report["status"] == "PASS"
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


def _workers_response(rows):
    return {
        "/api/v1/health": (200, {"data": {"status": "ok", "deployment_revision": "abc1234"}}),
        "/api/v1/readiness": (200, {"data": {
            "status": "ready", "environment": "staging", "deployment_revision": "abc1234",
            "checks": {"database": True, "schema_current": True, "deployment_revision_configured": True},
            "schema": {"database_revisions": ["0081"], "code_heads": ["0081"]},
        }}),
        "/api/v1/admin/operations/summary": (200, {"data": {
            "stale_running_acquisition_runs": 0, "expired_processing_outbox_events": 0,
            "workers": {"stale_count": 0},
        }}),
        "/api/v1/admin/operations/workers": (200, {"data": rows}),
    }


def _verify(monkeypatch, rows, **kwargs):
    responses = _workers_response(rows)
    monkeypatch.setattr(verifier, "_request_json", lambda base_url, path, **kw: responses[path])
    report = verifier.verify_runtime(
        base_url="https://staging.example.test", expected_revision="abc1234", admin_token="t",
        expected_workers={"outbox"}, required_integrations=set(), stale_after_seconds=120,
        timeout=5, **kwargs,
    )
    return next(c for c in report["checks"] if c["name"] == "worker_heartbeats")["status"]


def _ago(seconds):
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


def test_stopped_worker_is_unhealthy_by_default(monkeypatch):
    rows = [{"worker_name": "outbox", "status": "STOPPED", "last_success_at": _ago(30)}]
    assert _verify(monkeypatch, rows) == "FAIL"


def test_stopped_worker_with_a_fresh_successful_cycle_passes_when_opted_in(monkeypatch):
    rows = [{"worker_name": "outbox", "status": "STOPPED", "last_success_at": _ago(600)}]
    assert _verify(monkeypatch, rows, max_stopped_age_seconds=1200) == "PASS"


def test_stopped_worker_with_an_old_or_missing_success_fails_even_when_opted_in(monkeypatch):
    old = [{"worker_name": "outbox", "status": "STOPPED", "last_success_at": _ago(5000)}]
    never = [{"worker_name": "outbox", "status": "STOPPED", "last_success_at": None}]
    junk = [{"worker_name": "outbox", "status": "STOPPED", "last_success_at": "not-a-date"}]
    for rows in (old, never, junk):
        assert _verify(monkeypatch, rows, max_stopped_age_seconds=1200) == "FAIL"


def test_failed_worker_never_passes_even_with_a_fresh_success(monkeypatch):
    rows = [{"worker_name": "outbox", "status": "FAILED", "last_success_at": _ago(10)}]
    assert _verify(monkeypatch, rows, max_stopped_age_seconds=1200) == "FAIL"


def test_newest_row_per_worker_decides_regardless_of_response_order(monkeypatch):
    stale_old = {"worker_name": "outbox", "status": "STOPPED", "last_seen_at": _ago(90000), "last_success_at": _ago(90000)}
    fresh = {"worker_name": "outbox", "status": "STOPPED", "last_seen_at": _ago(60), "last_success_at": _ago(60)}
    for rows in ([stale_old, fresh], [fresh, stale_old]):
        assert _verify(monkeypatch, rows, max_stopped_age_seconds=1200) == "PASS"
