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


def _outbox_cycle(h, code=0):
    """Run one outbox cycle to completion and observe it at the current fake time."""
    h.host.tick()
    h.finish("outbox", code)
    h.now += 0.1
    h.host.tick()  # reap


def test_outbox_reruns_immediately_while_backlog_remains():
    h = Harness(poll_seconds=5.0)
    h.host.backlog_probe = lambda name: name == "outbox"
    _outbox_cycle(h)
    assert h.started.count("outbox") == 2  # restarted in the same tick it was reaped


def test_outbox_waits_poll_interval_when_no_backlog():
    h = Harness(poll_seconds=5.0)
    h.host.backlog_probe = lambda name: False
    _outbox_cycle(h)
    assert h.started.count("outbox") == 1
    h.now += 4.0
    h.host.tick()
    assert h.started.count("outbox") == 1
    h.now += 1.5
    h.host.tick()
    assert h.started.count("outbox") == 2


def test_failed_outbox_cycle_never_reruns_immediately():
    h = Harness(poll_seconds=5.0)
    h.host.backlog_probe = lambda name: True
    _outbox_cycle(h, code=1)
    assert h.started.count("outbox") == 1


def test_probe_error_falls_back_to_poll_interval():
    h = Harness(poll_seconds=5.0)

    def boom(_name):
        raise RuntimeError("db down")

    h.host.backlog_probe = boom
    _outbox_cycle(h)
    assert h.started.count("outbox") == 1


def test_only_outbox_is_backlog_aware_and_gets_its_own_batch_size():
    h = Harness(batch_size=7, outbox_batch_size=60)
    h.host.backlog_probe = lambda name: True
    assert h.host._options("outbox").batch_size == 60
    assert h.host._options("source_refresh_worker").batch_size == 7
    assert not h.host._backlog_remains("source_refresh_worker")


# ---- probe-gated starts (option B of the complexity review) ----

def _probed(**config):
    h = Harness(poll_seconds=5.0, safety_net_seconds=60.0, **config)
    h.asked = []
    h.has_work = set()

    def probe(name):
        h.asked.append(name)
        return name in h.has_work

    h.host.work_probe = probe
    return h


def test_first_cycle_always_starts_every_worker_to_establish_a_baseline():
    h = _probed()
    for _ in range(8):  # general lane is capped, so workers start in waves
        h.host.tick()
        _settle(h)
    assert set(h.started) == set(BATCH_WORKERS)
    assert h.asked == []  # no probe is consulted before a worker's first start


def _settle(h):
    for name in list(BATCH_WORKERS):
        h.finish(name)
    h.now += 0.1
    h.host.tick()


def test_idle_workers_are_not_spawned_between_safety_nets():
    h = _probed()
    for _ in range(8):  # baseline start of everything
        h.host.tick()
        _settle(h)
    baseline = len(h.started)
    for _ in range(10):  # 50 s idle, probe says no work anywhere
        h.now += 5.0
        h.host.tick()
        _settle(h)
    assert len(h.started) == baseline
    assert sum(s.skipped_idle for s in h.host.slots.values()) > 0


def test_worker_with_work_is_started_within_one_poll():
    h = _probed()
    for _ in range(8):
        h.host.tick()
        _settle(h)
    baseline = h.started.count("discovery_run_worker")
    h.has_work.add("discovery_run_worker")
    h.now += 5.1
    h.host.tick()
    assert h.started.count("discovery_run_worker") == baseline + 1


def test_safety_net_starts_every_worker_even_if_the_probe_always_says_no():
    h = _probed()
    for _ in range(8):
        h.host.tick()
        _settle(h)
    baseline = len(h.started)
    for _ in range(14):  # 70 s > 60 s safety net
        h.now += 5.0
        h.host.tick()
        _settle(h)
    assert len(h.started) - baseline == len(BATCH_WORKERS)


def test_probe_error_fails_open_and_starts_the_worker():
    h = Harness(poll_seconds=5.0, safety_net_seconds=60.0)

    def boom(_name):
        raise RuntimeError("db down")

    h.host.work_probe = boom
    for _ in range(8):
        h.host.tick()
        _settle(h)
    baseline = h.started.count("outbox")
    h.now += 5.1
    h.host.tick()
    assert h.started.count("outbox") == baseline + 1


def test_once_mode_ignores_probes():
    h = _probed()
    h.host.work_probe = lambda name: False
    h.host.tick(once_pending=set(BATCH_WORKERS))
    assert h.started.count("outbox") == 1


def test_safety_net_must_not_be_shorter_than_poll_interval():
    with pytest.raises(ValueError):
        HostConfig(poll_seconds=30.0, safety_net_seconds=10.0).validate()
