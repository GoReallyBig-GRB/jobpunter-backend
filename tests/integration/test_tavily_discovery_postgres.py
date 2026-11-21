"""Tavily company discovery: credit ledger, hard stops, board recording, registration and status."""
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.application.jobs import ats_boards
from app.application.jobs import tavily_discovery as td
from app.infrastructure.db.models import (
    AdminAuditEvent, DiscoveredAtsBoard, SearchCreditLedgerEntry, SearchDiscoverySettings, SearchDiscoveryTerm,
    Source, SourceConnector,
)
from app.infrastructure.search import tavily
from tests.integration.test_source_registry_admin_postgres import db, make_client, make_user  # noqa: F401

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="PostgreSQL integration test requires DATABASE_URL"
)
NOW = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)


@pytest.fixture(autouse=True)
def _settings_and_key(monkeypatch, db):  # noqa: F811
    monkeypatch.setenv("JOBPUNTER_TAVILY_API_KEY", "tvly-test-key")
    monkeypatch.setattr(td, "get_settings", lambda: SimpleNamespace(
        tavily_monthly_credit_limit=1000, tavily_hard_stop_fraction=0.9,
        tavily_daily_search_cap=20, tavily_min_minutes_between_searches=55,
    ))
    # Isolate from any rows in the shared test database.
    db.execute(SearchCreditLedgerEntry.__table__.delete())
    db.execute(DiscoveredAtsBoard.__table__.delete())
    db.execute(SearchDiscoveryTerm.__table__.delete())
    db.execute(SearchDiscoverySettings.__table__.delete())
    db.flush()


def _ledger(db, *, at, credits=1, outcome="SUCCEEDED", key="k", new=0):
    row = SearchCreditLedgerEntry(
        provider=td.PROVIDER, purpose=td.PURPOSE, query_key=key, query_text="q", credits_used=credits,
        outcome=outcome, result_count=0, new_boards_count=new, created_at=at,
    )
    db.add(row)
    db.flush()
    return row


def _search(urls, *, outcome="SUCCEEDED", credits=1):
    calls = []

    def fn(text, domains, max_results=20):
        calls.append((text, tuple(domains)))
        return tavily.TavilyResult(outcome == "SUCCEEDED", 200 if outcome == "SUCCEEDED" else 500, outcome,
                                   urls=list(urls), credits=credits)

    fn.calls = calls
    return fn


def _probe(ok_tokens=()):
    def fn(provider, token):
        if token in ok_tokens:
            return ats_boards.ProbeResult(True, job_count=7)
        return ats_boards.ProbeResult(False, reason="BOARD_NOT_FOUND")
    return fn


def test_budget_blocks_in_priority_order_and_is_otherwise_open(db):  # noqa: F811
    assert td.budget_status(db, NOW).can_search
    _ledger(db, at=NOW - timedelta(minutes=10))
    assert td.budget_status(db, NOW).blocked_reason == "TOO_SOON"
    assert td.budget_status(db, NOW + timedelta(minutes=60)).can_search
    for i in range(19):  # 20 searches today in total
        _ledger(db, at=NOW - timedelta(hours=11, minutes=i))
    assert td.budget_status(db, NOW + timedelta(hours=1)).blocked_reason == "DAILY_CAP"


def test_monthly_hard_stop_at_ninety_percent_of_the_limit(db):  # noqa: F811
    month_start = NOW.replace(day=1, hour=0)
    _ledger(db, at=month_start + timedelta(minutes=1), credits=899)
    assert td.budget_status(db, NOW).can_search
    _ledger(db, at=month_start + timedelta(minutes=2), credits=1)
    status = td.budget_status(db, NOW)
    assert status.blocked_reason == "MONTHLY_HARD_STOP" and status.hard_stop_credits == 900
    assert status.month_credits_used == 900


def test_previous_months_usage_does_not_count(db):  # noqa: F811
    _ledger(db, at=NOW.replace(day=1, hour=0) - timedelta(days=2), credits=900)
    assert td.budget_status(db, NOW).month_credits_used == 0


@pytest.mark.parametrize("outcome", ["LIMIT_EXCEEDED", "INVALID_KEY"])
def test_provider_refusal_stops_searching_for_a_day(db, outcome):  # noqa: F811
    _ledger(db, at=NOW - timedelta(hours=3), credits=0, outcome=outcome)
    assert td.budget_status(db, NOW).blocked_reason == "PROVIDER_LIMIT_OR_INVALID_KEY"
    assert td.budget_status(db, NOW + timedelta(hours=20)).blocked_reason == "PROVIDER_LIMIT_OR_INVALID_KEY"
    assert td.budget_status(db, NOW + timedelta(hours=22)).can_search  # the refusal is now over 24 h old


def test_step_records_ledger_and_probed_boards_and_skips_known_ones(db):  # noqa: F811
    user = make_user(db, admin=True)
    # An already registered Lever board and a Greenhouse board must not be rediscovered.
    for name, ctype, config in (
        ("Lever - Known", "JSON_API", {"endpoint_url": ats_boards.endpoint_for("lever", "knownco")}),
        ("GH - Known", "GREENHOUSE_JOB_BOARD", {"board_token": "ghknown"}),
    ):
        source = Source(name=f"{name}-{os.urandom(2).hex()}", source_type="EMPLOYER", status="ACTIVE",
                        permission_review_status="PENDING", operational_state="AVAILABLE")
        db.add(source)
        db.flush()
        db.add(SourceConnector(source_id=source.id, connector_type=ctype, connector_version="1",
                               status="ACTIVE", configuration=config))
    db.add(DiscoveredAtsBoard(provider="ashby", token="Seen", status="REJECTED", reject_reason="X",
                              found_via_url="https://jobs.ashbyhq.com/Seen", found_via_query_key="k"))
    db.flush()
    urls = [
        "https://jobs.lever.co/knownco/1", "https://boards.greenhouse.io/ghknown/jobs/1",
        "https://jobs.ashbyhq.com/seen/1",  # same board as the stored one, different case
        "https://jobs.lever.co/newgood/1", "https://jobs.lever.co/newgood/2",  # duplicate in one result set
        "https://jobs.lever.co/newbad/1", "https://example.com/noise",
    ]
    search = _search(urls)
    result = td.run_discovery_step(db, now=NOW, search_fn=search, probe_fn=_probe({"newgood"}))
    assert result.status == "SEARCHED" and result.outcome == "SUCCEEDED"
    assert (result.new_boards, result.probe_ok, result.rejected) == (2, 1, 1)
    assert len(search.calls) == 1 and search.calls[0][1] == tuple(td.INCLUDE_DOMAINS)
    boards = {b.token: b for b in db.scalars(select(DiscoveredAtsBoard))}
    assert boards["newgood"].status == "PROBE_OK" and boards["newgood"].open_jobs_seen == 7
    assert boards["newbad"].status == "REJECTED" and boards["newbad"].reject_reason == "BOARD_NOT_FOUND"
    ledger = db.scalar(select(SearchCreditLedgerEntry))
    assert (ledger.credits_used, ledger.outcome, ledger.result_count, ledger.new_boards_count) == (1, "SUCCEEDED", 7, 1)


def test_a_second_step_inside_the_window_makes_no_call(db):  # noqa: F811
    search = _search([])
    td.run_discovery_step(db, now=NOW, search_fn=search, probe_fn=_probe())
    second = td.run_discovery_step(db, now=NOW + timedelta(minutes=5), search_fn=search, probe_fn=_probe())
    assert second.status == "SKIPPED_TOO_SOON" and len(search.calls) == 1


def test_failed_calls_are_counted_conservatively_and_refusals_cost_nothing(db):  # noqa: F811
    td.run_discovery_step(db, now=NOW, search_fn=_search([], outcome="FAILED"), probe_fn=_probe())
    assert db.scalar(select(SearchCreditLedgerEntry)).credits_used == 1  # may have been billed
    db.execute(SearchCreditLedgerEntry.__table__.delete())
    td.run_discovery_step(db, now=NOW, search_fn=_search([], outcome="LIMIT_EXCEEDED"), probe_fn=_probe())
    row = db.scalar(select(SearchCreditLedgerEntry))
    assert (row.credits_used, row.outcome) == (0, "LIMIT_EXCEEDED")
    assert td.run_discovery_step(db, now=NOW + timedelta(hours=2), search_fn=_search([]), probe_fn=_probe()
                                 ).status == "SKIPPED_PROVIDER_LIMIT_OR_INVALID_KEY"


def test_queries_rotate_through_the_plan_without_repeating_early(db):  # noqa: F811
    seen = []
    for i in range(5):
        search = _search([])
        td.run_discovery_step(db, now=NOW + timedelta(hours=i), search_fn=search, probe_fn=_probe())
        seen.append(search.calls[0][0])
    assert len(set(seen)) == 5 and seen == [t for _k, t in td.QUERY_PLAN[:5]]


def test_a_query_with_three_zero_yield_runs_is_retired(db):  # noqa: F811
    first_key = td.QUERY_PLAN[0][0]
    for i in range(3):
        _ledger(db, at=NOW - timedelta(days=i + 1), key=first_key, new=0)
    for key, _text in td.QUERY_PLAN[1:]:  # everything else searched more recently
        _ledger(db, at=NOW - timedelta(hours=2), key=key, new=1)
    chosen = td.next_query(db)
    assert chosen is not None and chosen[0] != first_key


def test_probes_per_step_are_bounded(db):  # noqa: F811
    urls = [f"https://jobs.lever.co/co{i}/1" for i in range(td.MAX_PROBES_PER_STEP + 4)]
    result = td.run_discovery_step(db, now=NOW, search_fn=_search(urls), probe_fn=_probe())
    assert result.probe_ok + result.rejected == td.MAX_PROBES_PER_STEP
    assert db.scalar(select(func.count()).select_from(DiscoveredAtsBoard)) == td.MAX_PROBES_PER_STEP


def test_work_probe_is_false_without_a_key_and_follows_the_budget_guard(db, monkeypatch):  # noqa: F811
    from app.workers import work_probes

    def status(blocked):
        return td.BudgetStatus(0, 1000, 900, 0, 20, None, blocked)

    monkeypatch.delenv("JOBPUNTER_TAVILY_API_KEY")
    monkeypatch.setattr(td, "budget_status", lambda *_a, **_k: status(None))
    assert work_probes.work_may_exist(db, "tavily_discovery_worker", NOW) is False  # no key, so no work
    monkeypatch.setenv("JOBPUNTER_TAVILY_API_KEY", "tvly-test-key")
    assert work_probes.work_may_exist(db, "tavily_discovery_worker", NOW) is True
    monkeypatch.setattr(td, "budget_status", lambda *_a, **_k: status("DAILY_CAP"))
    assert work_probes.work_may_exist(db, "tavily_discovery_worker", NOW) is False


def test_admin_status_shows_limits_done_remaining_and_metrics_and_never_the_key(db, monkeypatch):  # noqa: F811
    url = "/api/v1/admin/discovery/tavily"
    assert make_client(db, make_user(db, admin=False)).get(url).status_code in (401, 403)
    monkeypatch.setattr(tavily, "fetch_usage", lambda **_k: {"available": True, "plan": "Researcher",
                                                             "plan_credits_used": 5, "plan_credit_limit": 1000})
    now = datetime.now(timezone.utc)
    _ledger(db, at=now - timedelta(minutes=1), credits=1, new=2, outcome="SUCCEEDED")
    _ledger(db, at=now - timedelta(minutes=2), credits=0, outcome="RATE_LIMITED")
    db.add(DiscoveredAtsBoard(provider="lever", token="a", status="PROBE_OK", found_via_url="u",
                              found_via_query_key="k"))
    db.add(DiscoveredAtsBoard(provider="workable", token="b", status="REGISTERED", found_via_url="u",
                              found_via_query_key="k"))
    db.flush()
    response = make_client(db, make_user(db, admin=True)).get(url)
    assert response.status_code == 200, response.text
    assert "tvly-test-key" not in response.text
    data = response.json()["data"]
    assert data["api_key_configured"] is True
    daily = data["daily"]
    assert daily["limit"] == 20 and daily["searches_done"] == 2 and daily["searches_remaining"] == 18
    assert daily["outcomes_today"] == {"SUCCEEDED": 1, "RATE_LIMITED": 1}
    assert daily["earliest_next_search_at"] is not None
    monthly = data["monthly"]
    assert monthly["credits_used"] == 1 and monthly["hard_stop_credits"] == 900
    assert monthly["credits_remaining_before_hard_stop"] == 899 and monthly["new_boards_found"] == 2
    assert monthly["searches"] == 2 and monthly["searches_succeeded"] == 1 and monthly["boards_per_credit"] == 2.0
    assert data["provider_usage"]["plan"] == "Researcher"
    assert data["discovered_boards"] == {"PROBE_OK": 1, "REGISTERED": 1}
    assert data["discovered_boards_by_provider"] == {"lever": 1, "workable": 1}
    assert data["last_seven_days"][0]["searches"] == 2 and data["last_seven_days"][0]["new_boards"] == 2
    assert data["recent_calls"][0]["outcome"] == "SUCCEEDED" and data["query_plan_size"] == len(td.QUERY_PLAN)


def test_status_reports_missing_key_honestly(db, monkeypatch):  # noqa: F811
    monkeypatch.delenv("JOBPUNTER_TAVILY_API_KEY")
    data = make_client(db, make_user(db, admin=True)).get("/api/v1/admin/discovery/tavily").json()["data"]
    assert data["api_key_configured"] is False and data["blocked_reason"] == "NO_API_KEY"
    assert data["can_search_now"] is False


def _fake_official_api(monkeypatch, good):
    import json

    def fetch(url):
        for token in good:
            if url.endswith("/" + token) or f"/{token}/jobs" in url:
                return 200, json.dumps([{"id": "1"}] if "lever" in url else {"jobs": [{"id": 1, "title": "t"}]}).encode()
        return 404, b"{}"

    monkeypatch.setattr(ats_boards, "_http_fetch", fetch)


def test_register_discovered_registers_probe_ok_boards_under_class_approval(db, monkeypatch):  # noqa: F811
    tag = os.urandom(3).hex()
    good, gone = f"lvgood{tag}", f"lvgone{tag}"
    for token in (good, gone):
        db.add(DiscoveredAtsBoard(provider="lever", token=token, status="PROBE_OK", found_via_url="u",
                                  found_via_query_key="k"))
    db.flush()
    _fake_official_api(monkeypatch, [good])
    client = make_client(db, make_user(db, admin=True))
    response = client.post("/api/v1/admin/sources/ats-boards/register-discovered",
                           json={"reason": "Register discovered boards", "limit": 5})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [r["token"] for r in data["registered"]] == [good]
    assert [r["token"] for r in data["rejected"]] == [gone]
    rows = {b.token: b for b in db.scalars(select(DiscoveredAtsBoard))}
    assert rows[good].status == "REGISTERED" and rows[gone].status == "REJECTED"
    source = db.scalar(select(Source).where(Source.id == data["registered"][0]["source_id"]))
    assert source.permission_review_reference == ats_boards.CLASS_APPROVAL_REFERENCE
    # Nothing left to register: a repeat call is a harmless no-op.
    again = client.post("/api/v1/admin/sources/ats-boards/register-discovered",
                        json={"reason": "Register discovered boards"}).json()["data"]
    assert again["registered"] == []


def test_register_discovered_refuses_when_refresh_capacity_is_reached(db, monkeypatch):  # noqa: F811
    from app.api.v1 import admin as admin_api

    db.add(DiscoveredAtsBoard(provider="lever", token="cap", status="PROBE_OK", found_via_url="u",
                              found_via_query_key="k"))
    db.flush()
    monkeypatch.setattr(admin_api, "MAX_ACTIVE_REFRESH_SCHEDULES", 0)
    response = make_client(db, make_user(db, admin=True)).post(
        "/api/v1/admin/sources/ats-boards/register-discovered", json={"reason": "Register discovered boards"})
    assert response.status_code == 409 and "capacity" in response.json()["detail"].lower()


def test_register_discovered_is_admin_only(db):  # noqa: F811
    response = make_client(db, make_user(db, admin=False)).post(
        "/api/v1/admin/sources/ats-boards/register-discovered", json={"reason": "Register discovered boards"})
    assert response.status_code in (401, 403)


BASE = "/api/v1/admin/discovery/tavily"


def _admin_client(db):  # noqa: F811
    return make_client(db, make_user(db, admin=True))


def test_pausing_stops_searching_and_resuming_restores_it(db):  # noqa: F811
    client = _admin_client(db)
    response = client.patch(f"{BASE}/settings", json={"enabled": False, "reason": "Pause while testing"})
    assert response.status_code == 200 and response.json()["data"]["enabled"] is False
    assert td.budget_status(db, NOW).blocked_reason == "PAUSED_BY_ADMIN"
    search = _search([])
    assert td.run_discovery_step(db, now=NOW, search_fn=search, probe_fn=_probe()).status == "SKIPPED_PAUSED_BY_ADMIN"
    assert search.calls == []
    client.patch(f"{BASE}/settings", json={"enabled": True, "reason": "Resume searching"})
    assert td.budget_status(db, NOW).can_search


def test_admin_can_regulate_daily_cap_and_spacing_and_the_guard_follows(db):  # noqa: F811
    client = _admin_client(db)
    data = client.patch(f"{BASE}/settings", json={
        "daily_search_cap": 2, "min_minutes_between_searches": 120, "reason": "Slow the search down",
    }).json()["data"]
    assert (data["daily_search_cap"], data["min_minutes_between_searches"], data["overridden_by_admin"]) == (2, 120, True)
    _ledger(db, at=NOW - timedelta(minutes=90))
    assert td.budget_status(db, NOW).blocked_reason == "TOO_SOON"  # 120-minute spacing now applies
    assert td.budget_status(db, NOW + timedelta(minutes=40)).can_search
    _ledger(db, at=NOW - timedelta(hours=5))
    assert td.budget_status(db, NOW + timedelta(minutes=40)).blocked_reason == "DAILY_CAP"
    audit = db.scalar(select(AdminAuditEvent).where(AdminAuditEvent.action == "TAVILY_DISCOVERY_SETTINGS_CHANGED"))
    assert audit.details["before"]["daily_search_cap"] == 20 and audit.details["after"]["daily_search_cap"] == 2
    assert audit.details["reason"] == "Slow the search down"
    # Resetting returns every control to the deployment default.
    reset = client.patch(f"{BASE}/settings", json={"use_defaults": True, "reason": "Back to default"}).json()["data"]
    assert reset["daily_search_cap"] == 20 and reset["overridden_by_admin"] is False


@pytest.mark.parametrize("body", [
    {"daily_search_cap": 30},          # 30 x 31 days would pass the 900-credit hard stop
    {"daily_search_cap": 0},
    {"min_minutes_between_searches": 5},
    {"reason": "No changes given"},
    {"use_defaults": True, "enabled": False},
    {"daily_search_cap": 5, "surprise": 1},
])
def test_unsafe_or_empty_settings_changes_are_rejected(db, body):  # noqa: F811
    response = _admin_client(db).patch(f"{BASE}/settings", json={"reason": "Test change", **body})
    assert response.status_code == 422, response.text
    assert db.scalar(select(func.count()).select_from(SearchDiscoverySettings)) == 0


def test_largest_cap_that_fits_the_monthly_hard_stop_is_accepted(db):  # noqa: F811
    assert td.max_allowed_daily_cap() == 29
    response = _admin_client(db).patch(f"{BASE}/settings", json={"daily_search_cap": 29, "reason": "Use the full budget"})
    assert response.status_code == 200


def test_controls_are_admin_only(db):  # noqa: F811
    client = make_client(db, make_user(db, admin=False))
    assert client.patch(f"{BASE}/settings", json={"enabled": False, "reason": "No rights"}).status_code in (401, 403)
    assert client.post(f"{BASE}/terms", json={"kind": "COMPANY", "text": "Acme", "reason": "No rights"}).status_code in (401, 403)
    assert client.patch(f"{BASE}/terms/{os.urandom(1).hex()}", json={"active": False, "reason": "x y z"}).status_code in (401, 403, 422)
    assert db.scalar(select(func.count()).select_from(SearchDiscoveryTerm)) == 0


def _add(client, kind, text):
    return client.post(f"{BASE}/terms", json={"kind": kind, "text": text, "reason": "Widen the search"})


def test_added_companies_and_queries_are_searched_first_and_once(db):  # noqa: F811
    client = _admin_client(db)
    assert _add(client, "COMPANY", "Dangote Group").status_code == 201
    assert _add(client, "QUERY", "graduate trainee programme Lagos").status_code == 201
    asked = []
    for i in range(3):
        search = _search([])
        td.run_discovery_step(db, now=NOW + timedelta(hours=i), search_fn=search, probe_fn=_probe())
        asked.append(search.calls[0][0])
    assert asked[0] == "Dangote Group careers jobs"
    assert asked[1] == "graduate trainee programme Lagos"
    assert asked[2] == td.QUERY_PLAN[0][1]  # then the normal rotation resumes; the two terms are not repeated


def test_a_failed_search_for_a_company_is_retried_not_forgotten(db):  # noqa: F811
    _add(_admin_client(db), "COMPANY", "Kuda Bank")
    td.run_discovery_step(db, now=NOW, search_fn=_search([], outcome="FAILED"), probe_fn=_probe())
    search = _search([])
    td.run_discovery_step(db, now=NOW + timedelta(hours=2), search_fn=search, probe_fn=_probe())
    assert search.calls[0][0] == "Kuda Bank careers jobs"


def test_added_sectors_and_markets_extend_the_rotation(db):  # noqa: F811
    client = _admin_client(db)
    base = len(td.QUERY_PLAN)
    _add(client, "SECTOR", "fashion and textiles")
    _add(client, "MARKET", "Rwanda")
    texts = td.rotation_texts(td._active_terms(db))
    assert len(texts) == base + len(td.MARKETS) + len(td.SECTORS) + 1  # new sector x all markets, new market x all sectors, plus the pair
    assert "fashion and textiles jobs Rwanda" in texts and "retail jobs Rwanda" in texts
    assert "fashion and textiles jobs Lagos" in texts


def test_deactivated_terms_stop_being_searched_and_can_be_reactivated(db):  # noqa: F811
    client = _admin_client(db)
    term_id = _add(client, "COMPANY", "Flutterwave").json()["data"]["id"]
    assert client.patch(f"{BASE}/terms/{term_id}", json={"active": False, "reason": "Not needed"}).json()["data"]["active"] is False
    assert td.priority_queries(td._active_terms(db)) == []
    assert db.get(SearchDiscoveryTerm, term_id).deactivated_at is not None
    client.patch(f"{BASE}/terms/{term_id}", json={"active": True, "reason": "Needed again"})
    assert td.priority_queries(td._active_terms(db)) == ["Flutterwave careers jobs"]
    actions = {a.action for a in db.scalars(select(AdminAuditEvent))}
    assert {"TAVILY_DISCOVERY_TERM_ADDED", "TAVILY_DISCOVERY_TERM_CHANGED"} <= actions
    assert client.patch(f"{BASE}/terms/{os.urandom(16).hex()[:8]}-0000-0000-0000-000000000000",
                        json={"active": False, "reason": "Unknown"}).status_code == 404


@pytest.mark.parametrize("kind,text", [
    ("COMPANY", "ceo@acme.com"), ("COMPANY", "https://acme.com/jobs"), ("QUERY", "www.acme.com"),
    ("COMPANY", "x"), ("COMPANY", "<script>"), ("MARKET", "a" * 121), ("COMPANY", "Acme; drop table"),
])
def test_terms_that_are_not_plain_names_or_phrases_are_rejected(db, kind, text):  # noqa: F811
    response = _add(_admin_client(db), kind, text)
    assert response.status_code == 422, response.text
    assert db.scalar(select(func.count()).select_from(SearchDiscoveryTerm)) == 0


def test_duplicate_terms_and_the_active_term_limit_are_enforced(db, monkeypatch):  # noqa: F811
    client = _admin_client(db)
    assert _add(client, "COMPANY", "Paystack").status_code == 201
    assert _add(client, "COMPANY", "  paystack ").status_code == 409  # same name, any case or spacing
    assert _add(client, "QUERY", "Paystack").status_code == 201        # a different kind is a different term
    monkeypatch.setattr(td, "MAX_ACTIVE_TERMS", 2)
    assert _add(client, "COMPANY", "Interswitch").status_code == 409


def test_status_lists_controls_and_terms_with_their_progress(db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(tavily, "fetch_usage", lambda **_k: {"available": False, "error": "REQUEST_FAILED"})
    client = _admin_client(db)
    _add(client, "COMPANY", "Dangote Group")
    _add(client, "COMPANY", "Nestle Nigeria")
    _add(client, "SECTOR", "fashion and textiles")
    _ledger(db, at=NOW - timedelta(hours=3), key=td._key("Dangote Group careers jobs"))
    data = client.get(BASE).json()["data"]
    controls = data["controls"]
    assert controls["enabled"] is True and controls["daily_search_cap"] == 20
    assert controls["max_daily_search_cap_allowed"] == 29 and controls["min_minutes_between_searches_allowed"] == 15
    status = {t["text"]: t["status"] for t in data["terms"]}
    assert status == {"Dangote Group": "SEARCHED", "Nestle Nigeria": "WAITING", "fashion and textiles": "IN_ROTATION"}
    assert data["provider_usage"] == {"available": False, "error": "REQUEST_FAILED"}
