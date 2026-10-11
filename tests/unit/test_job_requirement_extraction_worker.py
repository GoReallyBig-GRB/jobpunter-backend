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


def test_background_extraction_uses_governed_task_and_validates_source_excerpt(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    import app.workers.job_requirement_extraction_worker as worker_module

    class FakeSession:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False

    calls = []
    async def fake_execute(request, *, db, user_id, request_id):
        calls.append({
            "task": request.task,
            "payload": request.prompt,
            "user_id": user_id,
            "request_id": request_id,
        })
        return SimpleNamespace(parsed_data={"requirements": [{
            "requirement_type": "SKILL",
            "description": "Experience with Python",
            "source_excerpt": "three years of Python experience",
            "importance": "HIGH",
            "hard_constraint": True,
            "confidence": 0.91,
        }]})

    monkeypatch.setattr(worker_module, "execute_ai_task", fake_execute)
    worker = JobRequirementExtractionWorker(session_factory=FakeSession)
    version_id = uuid4()
    result = worker._extract_with_governance(
        title="Backend Engineer",
        company="Example Ltd",
        description="The successful candidate will build and maintain backend services using Python. You will collaborate with product and engineering teams, troubleshoot production issues, and write automated tests. Applicants must have three years of Python experience and a bachelor degree or equivalent.",
        version_id=version_id,
    )

    assert calls[0]["task"] == "job_requirement_extraction"
    assert calls[0]["user_id"] is None
    assert calls[0]["request_id"] == f"job-requirement-extraction:{version_id}"
    assert result[0].requirement_type == "SKILL"
    assert result[0].confidence == 0.91


def test_background_extraction_rejects_ungrounded_source_excerpt(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    import app.workers.job_requirement_extraction_worker as worker_module

    class FakeSession:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False

    async def fake_execute(*_args, **_kwargs):
        return SimpleNamespace(parsed_data={"requirements": [{
            "requirement_type": "SKILL",
            "description": "Experience with Python",
            "source_excerpt": "Invented source text",
            "importance": "HIGH",
            "hard_constraint": True,
            "confidence": 0.91,
        }]})

    monkeypatch.setattr(worker_module, "execute_ai_task", fake_execute)
    worker = JobRequirementExtractionWorker(session_factory=FakeSession)

    with pytest.raises(ValueError, match="ungrounded source excerpt"):
        worker._extract_with_governance(
            title="Backend Engineer",
            company=None,
            description="The successful candidate will build and maintain backend services using Python. You will collaborate with product and engineering teams, troubleshoot production issues, and write automated tests. Applicants must have three years of Python experience and a bachelor degree or equivalent.",
            version_id=uuid4(),
        )
