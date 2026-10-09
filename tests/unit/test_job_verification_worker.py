import pytest

from app.workers.job_verification_worker import run_worker


def test_verification_worker_once_runs_one_cycle():
    class FakeWorker:
        calls = 0

        def run_once(self, limit):
            self.calls += 1
            assert limit == 4
            return {"checked": 0, "active": 0, "closed": 0, "unknown": 0, "failed": 0}

    fake = FakeWorker()
    sleeps = []
    cycles = run_worker(
        once=True,
        batch_size=4,
        worker=fake,
        sleep=sleeps.append,
        should_stop=lambda: False,
    )

    assert cycles == 1
    assert fake.calls == 1
    assert sleeps == []


def test_verification_worker_rejects_invalid_limits():
    with pytest.raises(ValueError):
        run_worker(interval_seconds=0)
    with pytest.raises(ValueError):
        run_worker(batch_size=0)
