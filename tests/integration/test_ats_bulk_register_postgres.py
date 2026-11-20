"""Bulk registration of public ATS boards under the class approval (probe, approve, schedule)."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from app.application.jobs import ats_boards
from app.application.jobs.source_governance import has_approved_permission_review
from app.infrastructure.db.models import AdminAuditEvent, DiscoveryTarget, Source, SourceConnector
from app.infrastructure.db.models.discovery_schedule import SourceRefreshSchedule
from tests.integration.test_source_registry_admin_postgres import db, make_client, make_user  # noqa: F401

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="PostgreSQL integration test requires DATABASE_URL"
)
URL = "/api/v1/admin/sources/ats-boards/bulk-register"


def _fake(monkeypatch, known):
    def fetch(url):
        for token, (status, payload) in known.items():
            if url.endswith("/" + token) or f"/{token}/jobs" in url:
                import json
                return status, json.dumps(payload).encode()
        return 404, b"{}"

    monkeypatch.setattr(ats_boards, "_http_fetch", fetch)


def test_valid_boards_are_registered_approved_scheduled_and_rest_rejected(db, monkeypatch):  # noqa: F811
    admin = make_user(db, admin=True)
    tag = os.urandom(3).hex()
    good_l, good_a, good_g, dead, empty = f"lv{tag}", f"as{tag}", f"gh{tag}", f"dead{tag}", f"em{tag}"
    _fake(monkeypatch, {
        good_l: (200, [{"id": "1"}]), good_a: (200, {"jobs": [{"title": "t"}]}),
        good_g: (200, {"jobs": [{"id": 1}]}), empty: (200, {"jobs": []}),
    })
    client = make_client(db, admin)
    body = {"reason": "Seed public ATS boards", "boards": [
        {"provider": "lever", "token": good_l}, {"provider": "ashby", "token": good_a},
        {"provider": "greenhouse", "token": good_g}, {"provider": "lever", "token": dead},
        {"provider": "ashby", "token": empty},
    ]}
    response = client.post(URL, json=body)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["registered"]) == 3
    assert {r["token"]: r["reason"] for r in data["rejected"]} == {dead: "BOARD_NOT_FOUND", empty: "NO_OPEN_JOBS"}
    for row in data["registered"]:
        source = db.get(Source, row["source_id"])
        assert has_approved_permission_review(source)
        assert source.permission_review_reference == ats_boards.CLASS_APPROVAL_REFERENCE
        schedule = db.scalar(select(SourceRefreshSchedule).where(SourceRefreshSchedule.source_id == source.id))
        assert schedule.status == "ACTIVE" and schedule.interval_minutes == 360
        assert db.scalar(select(SourceConnector).where(SourceConnector.source_id == source.id)) is not None
        target = db.scalar(select(DiscoveryTarget).where(DiscoveryTarget.source_id == source.id))
        assert target.status == "ACTIVE" and target.target_kind == "SEARCH"
        assert target.url == ats_boards.endpoint_for(row["provider"], row["token"])
    audit = db.scalar(select(AdminAuditEvent).where(
        AdminAuditEvent.action == "ATS_BOARD_BULK_REGISTER", AdminAuditEvent.actor_user_id == admin.id))
    assert audit.details["registered"] == 3 and audit.details["rejected"] == 2
    # Idempotent: a second call registers nothing and reports them as already registered.
    again = client.post(URL, json={**body, "boards": body["boards"][:3]}).json()["data"]
    assert again["registered"] == [] and len(again["skipped"]) == 3


def test_non_admin_cannot_bulk_register_and_invalid_input_is_rejected(db, monkeypatch):  # noqa: F811
    _fake(monkeypatch, {})
    user = make_user(db, admin=False)
    client = make_client(db, user)
    ok_body = {"reason": "Seed public ATS boards", "boards": [{"provider": "lever", "token": "acme"}]}
    assert client.post(URL, json=ok_body).status_code in (401, 403)
    admin = make_user(db, admin=True)
    client = make_client(db, admin)
    for bad in [
        {**ok_body, "boards": [{"provider": "workday", "token": "acme"}]},
        {**ok_body, "boards": [{"provider": "lever", "token": "../x"}]},
        {**ok_body, "boards": []},
        {**ok_body, "boards": [{"provider": "lever", "token": f"t{i}"} for i in range(26)]},
    ]:
        assert client.post(URL, json=bad).status_code == 422


def test_existing_source_without_a_target_is_repaired_so_refresh_can_run(db, monkeypatch):  # noqa: F811
    admin = make_user(db, admin=True)
    token = f"sp{os.urandom(3).hex()}"
    _fake(monkeypatch, {token: (200, [{"id": "1"}])})
    kind, config = ats_boards.connector_for("lever", token, ats_boards.display_name(token))
    source = Source(name=f"Lever - {ats_boards.display_name(token)}", source_type=kind, status="ACTIVE")
    db.add(source)
    db.flush()
    db.add(SourceConnector(source_id=source.id, connector_type=kind, connector_version="1.0.0",
                           status="ACTIVE", configuration=config))
    db.flush()
    client = make_client(db, admin)
    data = client.post(URL, json={"reason": "Repair missing target", "boards": [
        {"provider": "lever", "token": token}]}).json()["data"]
    assert data["skipped"][0]["target_repaired"] is True
    target = db.scalar(select(DiscoveryTarget).where(DiscoveryTarget.source_id == source.id))
    assert target.status == "ACTIVE"
    again = client.post(URL, json={"reason": "Repair missing target", "boards": [
        {"provider": "lever", "token": token}]}).json()["data"]
    assert again["skipped"][0]["target_repaired"] is False
