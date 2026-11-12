"""Low-duty idle mode: batched database touches so a scale-to-zero database can suspend."""
import pytest

from app.workers.worker_host import HostConfig
from tests.unit.test_worker_host import Harness

IDLE = {"poll_seconds": 5.0, "safety_net_seconds": 60.0, "idle_poll_seconds": 900.0,
        "idle_after_seconds": 300.0}


def _run(h, seconds, step=1.0, on_probe=None):
    """Advance the clock; every started cycle exits cleanly on the next step."""
    probe_times = []

    def probe(name):
        probe_times.append(h.now)
        return on_probe(name, h.now) if on_probe else False

    h.host.work_probe = probe
    while h.now < seconds:
        for process in h.procs:
            if process.code is None:
                process.code = 0
        h.host.tick()
        h.now += step
    return probe_times


def _bursts(times, gap=1.5):
    bursts = []
    for t in times:
        if not bursts or t - bursts[-1][-1] > gap:
            bursts.append([t])
        else:
            bursts[-1].append(t)
    return bursts


def test_idle_mode_collapses_probes_into_a_few_bursts():
    h = Harness(**IDLE)
    times = _run(h, 3600)
    late = [t for t in times if t >= 400]
    assert len(_bursts(late)) <= 4  # roughly one wake-up per 900 s, not one probe round per 5 s
    baseline = Harness(poll_seconds=5.0, safety_net_seconds=60.0)
    baseline_late = [t for t in _run(baseline, 3600) if t >= 400]
    assert len(_bursts(baseline_late)) > 100  # the legacy cadence for comparison


def test_disabled_by_default_keeps_the_legacy_cadence():
    h = Harness(poll_seconds=5.0, safety_net_seconds=60.0)
    late = [t for t in _run(h, 3600) if t >= 1800]
    assert len(_bursts(late)) > 100


def test_safety_net_starts_are_stretched_while_idle_and_do_not_end_idle_mode():
    h = Harness(**IDLE)
    _run(h, 400)  # idle mode has begun
    before = {name: h.started.count(name) for name in h.host.slots}
    _run(h, 7600)
    extra = {name: h.started.count(name) - before[name] for name in h.host.slots}
    # about one safety-net start per 4 x idle_poll_seconds (3600 s) over 7200 s, not one per 60 s
    assert all(1 <= count <= 3 for count in extra.values()), extra
    assert h.host._idle_mode(h.now - 1) is True


def test_work_found_at_a_wake_up_ends_idle_mode_for_every_worker():
    h = Harness(**IDLE)
    found = {}

    def on_probe(name, now):
        if now >= 1700 and name == "outbox" and "at" not in found:
            found["at"] = now
            return True
        return False

    times = _run(h, 3200, on_probe=on_probe)
    assert "at" in found
    after = [t for t in times if found["at"] < t < found["at"] + 120]
    # after work appeared the host probes again at the normal cadence (many rounds, not one burst)
    assert len(_bursts(after)) >= 10


def test_idle_mode_starts_only_after_the_quiet_period():
    h = Harness(**IDLE)
    early = _run(h, 250)
    assert len(_bursts(early)) > 20  # still polling normally before idle_after_seconds


def test_heartbeat_reports_idle_mode_metrics():
    beats = []
    h = Harness(**IDLE)
    h.host.heartbeat = lambda status, metrics, error, ok: beats.append((status, metrics))
    _run(h, 1000)
    h.host._beat("IDLE")
    _status, metrics = beats[-1]
    assert metrics["idle_mode"] is True
    assert metrics["idle_poll_seconds"] == 900.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"idle_poll_seconds": 1.0},  # below poll_seconds
        {"idle_poll_seconds": 3601.0},
        {"idle_poll_seconds": 900.0, "idle_after_seconds": -1.0},
        {"idle_poll_seconds": 900.0, "idle_after_seconds": 90000.0},
    ],
)
def test_invalid_idle_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        HostConfig(**kwargs).validate()
