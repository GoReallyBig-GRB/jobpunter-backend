import pytest

from app.workers.discovery_run_worker import run_worker


class FakeWorker:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def run_once(self, limit):
        self.calls.append(limit)
        return self.outcome


def test_discovery_run_worker_once_reports_one_cycle_without_sleep():
    worker = FakeWorker({"claimed": 1, "completed": 1, "retried": 0, "failed": 0})
    sleeps = []
    heartbeats = []
    assert run_worker(
        once=True, batch_size=4, worker=worker, sleep=sleeps.append,
        heartbeat=lambda *args, **kwargs: heartbeats.append((args, kwargs)),
    ) == 1
    assert worker.calls == [4]
    assert sleeps == []
    assert heartbeats[-1][0][0] == "STOPPED"


@pytest.mark.parametrize("interval,batch", [(0, 1), (1, 0)])
def test_discovery_run_worker_rejects_invalid_polling_limits(interval, batch):
    with pytest.raises(ValueError):
        run_worker(interval_seconds=interval, batch_size=batch, once=True, worker=FakeWorker({}))
