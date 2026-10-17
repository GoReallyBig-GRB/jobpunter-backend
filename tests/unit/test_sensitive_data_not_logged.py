import logging
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import deps


def test_rejected_bearer_secret_is_not_written_to_application_logs(monkeypatch, caplog):
    secret = "SYNTHETIC_BEARER_SECRET_93c8d1"
    monkeypatch.setenv("JOBPUNTER_ENVIRONMENT", "production")
    monkeypatch.delenv("JOBPUNTER_ALLOW_DEV_IDENTITY", raising=False)

    class RejectingProvider:
        def authenticate(self, authorization, _db):
            raise ValueError(f"provider rejection included credential {secret}")

    monkeypatch.setattr(deps, "FirebaseIdentityProvider", RejectingProvider)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(HTTPException) as raised:
        deps.get_principal(
            authorization=f"Bearer {secret}",
            x_user_id=str(uuid4()),
            db=object(),
        )

    assert raised.value.status_code == 401
    assert raised.value.detail == "Invalid or missing authentication token"
    assert secret not in caplog.text

def test_worker_exception_message_is_not_written_to_application_logs(monkeypatch, caplog):
    from types import SimpleNamespace

    from app.workers import discovery_evaluation_worker

    secret_marker = "SYNTHETIC_PRIVATE_WORKER_ERROR_7a91"
    mission = SimpleNamespace(
        id=uuid4(), status="AWAITING_EVALUATION", updated_at=None, error_code=None
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def scalars(self, _statement):
            return SimpleNamespace(all=lambda: [mission])

        def get(self, _model, _record_id):
            return mission

        def commit(self):
            return None

    sessions = [FakeSession(), FakeSession(), FakeSession()]

    def session_factory():
        return sessions.pop(0)

    def fail_with_private_content(*_args, **_kwargs):
        raise RuntimeError(f"provider response contained {secret_marker}")

    monkeypatch.setattr(
        discovery_evaluation_worker,
        "evaluate_discovery_mission",
        fail_with_private_content,
    )
    caplog.set_level(logging.ERROR, logger=discovery_evaluation_worker.logger.name)

    result = discovery_evaluation_worker.DiscoveryEvaluationWorker(
        session_factory=session_factory, matcher=object()
    ).run_once(limit=1)

    assert result == {"claimed": 1, "completed": 0, "failed": 1}
    assert mission.error_code == "RuntimeError"
    assert secret_marker not in caplog.text
    assert "RuntimeError" in caplog.text

def test_private_document_modules_do_not_log_exception_tracebacks():
    import inspect

    from app.application.cv_studio import documents as cv_documents
    from app.application.documents import imports, restore, service

    for module in (cv_documents, imports, restore, service):
        assert "logger.exception(" not in inspect.getsource(module), module.__name__
