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
