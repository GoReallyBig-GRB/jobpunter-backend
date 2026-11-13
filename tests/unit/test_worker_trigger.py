import subprocess
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import internal_worker
from app.application.operations.worker_trigger import (
    TRIGGER_WORKERS,
    TriggerLimits,
    WorkerCycleRunner,
    available_memory_mb,
)
from app.config.settings import Settings
from app.main import app
from app.workers.bounded_runner import WORKERS

LIMITS = TriggerLimits(batch_size=3, worker_timeout_seconds=30, total_seconds=100, min_free_mb=200)
SECRET = "s" * 40


class FakeProcess:
    def __init__(self, code=0, hang=None):
        self.code, self.hang = code, hang

    def wait(self, timeout=None):
        if self.hang is not None:
            self.hang.wait(5)
        if self.code == "timeout":
            raise subprocess.TimeoutExpired("w", timeout)
        return self.code


def _runner(starter, memory=lambda: 1000.0, workers=("outbox", "discovery_scheduler"), **kw):
    stopped = []
    runner = WorkerCycleRunner(
        kw.pop("limits", LIMITS),
        memory_probe=memory,
        starter=starter,
        stopper=lambda p, g: stopped.append(p),
        workers=workers,
        **kw,
    )
    runner.stopped = stopped
    return runner


def _finish(runner):
    runner._thread.join(5)
    return runner.status()


def test_sequence_is_allowlisted_and_excludes_browser_capable_and_retention_workers():
    assert set(TRIGGER_WORKERS) <= set(WORKERS)
    assert "discovery_mission_worker" not in TRIGGER_WORKERS
    assert "document_retention_worker" not in TRIGGER_WORKERS


def test_cycle_runs_each_worker_in_order_with_configured_batch_size():
    seen = []

    def starter(options):
        seen.append((options.worker, options.batch_size))
        return FakeProcess(0)

    runner = _runner(starter)
    assert runner.start()[0] == "STARTED"
    status = _finish(runner)
    assert status["state"] == "FINISHED"
    assert seen == [("outbox", 3), ("discovery_scheduler", 3)]
    assert [r["status"] for r in status["results"]] == ["OK", "OK"]


def test_failed_worker_is_reported_and_does_not_stop_the_cycle():
    codes = iter([1, 0])
    runner = _runner(lambda o: FakeProcess(next(codes)))
    runner.start()
    assert [r["status"] for r in _finish(runner)["results"]] == ["FAILED", "OK"]


def test_second_start_while_running_is_refused_and_only_one_process_runs_at_a_time():
    gate = threading.Event()
    active = []
    peak = []

    def starter(options):
        active.append(options.worker)
        peak.append(len(active))
        proc = FakeProcess(0, hang=gate)
        original = proc.wait

        def wait(timeout=None):
            try:
                return original(timeout)
            finally:
                active.pop()

        proc.wait = wait
        return proc

    runner = _runner(starter)
    assert runner.start()[0] == "STARTED"
    assert runner.start() == ("BUSY", None)
    gate.set()
    _finish(runner)
    assert max(peak) == 1


def test_start_refused_when_memory_is_low_or_unmeasurable():
    started = []
    for probe in (lambda: 50.0, lambda: None):
        runner = _runner(lambda o: started.append(o) or FakeProcess(0), memory=probe)
        assert runner.start() == ("LOW_MEMORY", None)
    assert started == []


def test_worker_is_skipped_when_memory_drops_mid_cycle():
    levels = iter([1000.0, 1000.0, 50.0])  # start check, first worker, second worker
    runner = _runner(lambda o: FakeProcess(0), memory=lambda: next(levels))
    runner.start()
    assert [r["status"] for r in _finish(runner)["results"]] == ["OK", "SKIPPED_LOW_MEMORY"]


def test_timeout_stops_the_process_tree_and_continues():
    procs = [FakeProcess("timeout"), FakeProcess(0)]
    runner = _runner(lambda o: procs.pop(0))
    first = procs[0]
    runner.start()
    results = _finish(runner)["results"]
    assert [r["status"] for r in results] == ["TIMEOUT", "OK"]
    assert runner.stopped == [first]


def test_start_failure_is_reported_without_leaking_details():
    def starter(options):
        raise OSError("secret path /x/y")

    runner = _runner(starter, workers=("outbox",))
    runner.start()
    result = _finish(runner)["results"][0]
    assert result == {"worker": "outbox", "status": "START_FAILED"}


def test_total_deadline_skips_remaining_workers():
    now = [0.0]

    def starter(options):
        now[0] += 99.0
        return FakeProcess(0)

    runner = _runner(starter, clock=lambda: now[0])
    runner.start()
    assert [r["status"] for r in _finish(runner)["results"]] == ["OK", "SKIPPED_DEADLINE"]


def test_available_memory_probe_returns_a_non_negative_number_or_none():
    value = available_memory_mb()
    assert value is None or value >= 0


@pytest.mark.parametrize("value", ["short", "x" * 31])
def test_settings_reject_a_short_trigger_secret(value):
    with pytest.raises(ValueError):
        Settings(worker_trigger_secret=value)


def test_settings_treat_a_blank_trigger_secret_as_disabled():
    assert Settings(worker_trigger_secret="  ").worker_trigger_secret is None
    assert Settings(worker_trigger_secret=SECRET).worker_trigger_secret == SECRET


class FakeRunner:
    def __init__(self, outcome="STARTED"):
        self.outcome = outcome

    def start(self):
        return self.outcome, ("run-1" if self.outcome == "STARTED" else None)

    def status(self):
        return {"state": "NEVER_RUN"}


@pytest.fixture
def client(monkeypatch):
    def configure(secret=SECRET, outcome="STARTED"):
        monkeypatch.setattr(
            internal_worker, "get_settings", lambda: SimpleNamespace(worker_trigger_secret=secret)
        )
        monkeypatch.setattr(internal_worker, "get_runner", lambda: FakeRunner(outcome))
        return TestClient(app)

    return configure


def test_route_does_not_exist_when_no_secret_is_configured(client):
    c = client(secret=None)
    assert c.post("/api/v1/internal/worker-cycle", headers={"X-Worker-Trigger-Secret": SECRET}).status_code == 404
    assert c.get("/api/v1/internal/worker-cycle").status_code == 404


@pytest.mark.parametrize("headers", [{}, {"X-Worker-Trigger-Secret": "wrong"}, {"X-Worker-Trigger-Secret": ""}])
def test_route_rejects_missing_or_wrong_secret(client, headers):
    c = client()
    assert c.post("/api/v1/internal/worker-cycle", headers=headers).status_code == 403
    assert c.get("/api/v1/internal/worker-cycle", headers=headers).status_code == 403


def test_route_starts_a_cycle_with_the_right_secret(client):
    response = client().post("/api/v1/internal/worker-cycle", headers={"X-Worker-Trigger-Secret": SECRET})
    assert response.status_code == 202
    assert response.json() == {"status": "STARTED", "run_id": "run-1"}


@pytest.mark.parametrize("outcome,code", [("BUSY", 409), ("LOW_MEMORY", 503)])
def test_route_maps_refusals_to_status_codes(client, outcome, code):
    response = client(outcome=outcome).post(
        "/api/v1/internal/worker-cycle", headers={"X-Worker-Trigger-Secret": SECRET}
    )
    assert response.status_code == code


def test_status_route_returns_runner_state(client):
    response = client().get("/api/v1/internal/worker-cycle", headers={"X-Worker-Trigger-Secret": SECRET})
    assert response.status_code == 200
    assert response.json() == {"state": "NEVER_RUN"}
