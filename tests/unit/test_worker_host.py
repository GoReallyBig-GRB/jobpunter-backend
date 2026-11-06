import pytest

from app.workers import worker_host
from app.workers.bounded_runner import BATCH_WORKERS
from app.workers.worker_host import GENERAL_WORKERS, RESERVED_WORKERS, HostConfig, WorkerHost


class FakeProcess:
    def __init__(self, name):
        self.name = name
        self.code = None

    def poll(self):
        return self.code


class Harness:
    def __init__(self, **config):
        self.now = 0.0
        self.started = []
        self.procs = []
        self.stopped = []
        self.host = WorkerHost(
            HostConfig(**config),
            spawn=self._spawn,
            stop=lambda p, _g: self.stopped.append(p.name) or setattr(p, "code", -9),
            clock=lambda: self.now,
        )

    def _spawn(self, options):
        proc = FakeProcess(options.worker)
        self.started.append(options.worker)
        self.procs.append(proc)
        return proc

    def finish(self, name, code=0):
        for p in self.procs:
            if p.name == name and p.code is None:
                p.code = code


def test_lanes_cover_every_batch_worker_exactly_once():
    assert set(RESERVED_WORKERS) | set(GENERAL_WORKERS) == set(BATCH_WORKERS)
    assert not set(RESERVED_WORKERS) & set(GENERAL_WORKERS)


def test_reserved_workers_start_even_when_general_lane_is_saturated():
    h = Harness(general_concurrency=2)
    h.host.tick()
    assert set(RESERVED_WORKERS) <= set(h.started)
    assert len([n for n in h.started if n in GENERAL_WORKERS]) == 2
    # general lane stays saturated; reserved worker re-runs promptly after finishing
    h.finish("outbox")
    h.now = 6.0
    h.host.tick()  # observes the exit; next cycle due after poll_seconds
    h.now = 12.0
    h.host.tick()
    assert h.started.count("outbox") == 2


def test_general_concurrency_ceiling_is_never_exceeded():
    h = Harness(general_concurrency=3)
    for step in range(30):
        h.now += 6.0
        h.host.tick()
        active_general = [s for s in h.host.active() if not s.reserved]
        assert len(active_general) <= 3
        for s in list(active_general)[:1]:
            h.finish(s.name)


def test_every_general_worker_is_eventually_scheduled():
    h = Harness(general_concurrency=1)
    for _ in range(40):
        h.now += 6.0
        h.host.tick()
        for s in h.host.active():
            h.finish(s.name)
    assert set(GENERAL_WORKERS) <= set(h.started)


def test_deadline_stops_process_and_counts_failure():
    h = Harness(worker_timeout_seconds=30.0, termination_grace_seconds=1.0)
    h.host.tick()
    h.now = 31.0
    h.host.tick()
    assert "outbox" in h.stopped
    assert h.host.slots["outbox"].last_exit == 124
    assert h.host.slots["outbox"].failures == 1


def test_run_once_runs_each_worker_once_and_reports_failure(monkeypatch):
    monkeypatch.setattr(worker_host.time, "sleep", lambda _s: None)
    h = Harness(general_concurrency=2)
    real_tick = h.host.tick

    def tick(**kw):
        for p in h.procs:
            if p.code is None:
                p.code = 1 if p.name == "job_verification_worker" else 0
        real_tick(**kw)

    h.host.tick = tick
    assert h.host.run_once() == 1
    assert sorted(h.started) == sorted(BATCH_WORKERS)


def test_shutdown_terminates_every_active_process():
    beats = []
    h = Harness()
    h.host.heartbeat = lambda *a: beats.append(a[0])
    h.host.tick()
    active = {s.name for s in h.host.active()}
    h.host.shutdown()
    assert set(h.stopped) == active
    assert not h.host.active()
    assert beats[-1] == "STOPPED"


@pytest.mark.parametrize("kwargs", [
    {"general_concurrency": 0},
    {"general_concurrency": 99},
    {"poll_seconds": 0},
    {"worker_timeout_seconds": 5.0, "termination_grace_seconds": 10.0},
])
def test_invalid_config_rejected(kwargs):
    with pytest.raises(ValueError):
        HostConfig(**kwargs).validate()


def test_host_gives_each_worker_a_stable_distinct_heartbeat_identity(monkeypatch):
    seen = []

    class P:
        def poll(self):
            return 0

    def fake_start(options, env=None):
        seen.append((options.worker, env[worker_host.INSTANCE_ID_ENV]))
        return P()

    monkeypatch.setattr(worker_host, "start_process", fake_start)
    host = WorkerHost(HostConfig(poll_seconds=0.05), clock=iter(range(0, 1000, 1)).__next__)
    for _ in range(40):
        host.tick()
    by_worker = {}
    for name, ident in seen:
        by_worker.setdefault(name, set()).add(ident)
    assert all(len(ids) == 1 for ids in by_worker.values())  # stable across cycles
    assert len({next(iter(i)) for i in by_worker.values()}) == len(by_worker)  # distinct
    assert max(sum(1 for n, _ in seen if n == w) for w in by_worker) > 1  # cycled repeatedly
