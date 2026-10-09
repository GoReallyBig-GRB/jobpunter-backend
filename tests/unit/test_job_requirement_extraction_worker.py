import pytest

from app.workers.job_requirement_extraction_worker import (
    JobRequirementExtractionWorker,
    run_worker,
)


def test_requirement_extraction_worker_once_runs_one_cycle():
    class FakeWorker:
        calls = 0

        def run_once(self, limit):
            self.calls += 1
            assert limit == 5
            return {
                "claimed": 0,
                "needs_review": 0,
                "no_requirements": 0,
                "failed": 0,
                "skipped": 0,
                "disabled": 0,
            }

    fake = FakeWorker()
    sleeps = []
    cycles = run_worker(
        once=True,
        batch_size=5,
        worker=fake,
        sleep=sleeps.append,
        should_stop=lambda: False,
    )

    assert cycles == 1
    assert fake.calls == 1
    assert sleeps == []


def test_requirement_extraction_worker_rejects_invalid_limits():
    with pytest.raises(ValueError):
        run_worker(interval_seconds=0)
    with pytest.raises(ValueError):
        run_worker(batch_size=0)


def test_worker_stays_disabled_without_provider_configuration(monkeypatch):
    for key in (
        "JOBPUNTER_MATCHER_ENDPOINT",
        "JOBPUNTER_MATCHER_API_KEY",
        "JOBPUNTER_MATCHER_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    worker = JobRequirementExtractionWorker(session_factory=None)
    outcome = worker.run_once(limit=7)

    assert outcome["claimed"] == 0
    assert outcome["disabled"] == 1
    assert outcome["skipped"] == 0
