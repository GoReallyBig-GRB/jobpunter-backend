from uuid import uuid4

from app.workers.acquisition import run_acquisition


def test_run_acquisition_returns_safe_result_fields(monkeypatch):
    expected = type("Result", (), {
        "run_id": uuid4(), "status": "SUCCEEDED", "acquired_count": 12, "persisted_count": 9,
        "error_category": None, "failed_item_count": 0, "retry_after_seconds": None,
    })()

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeService:
        def __init__(self, _repository):
            pass

        def run(self, source_id, connector_id, run_key, context):
            assert source_id == source
            assert connector_id == connector
            assert run_key == "mission-2026-10-09"
            assert context == {"search_terms": ["Technical Product Manager"], "location": "Nigeria"}
            return expected

    source, connector = uuid4(), uuid4()
    monkeypatch.setattr("app.workers.acquisition.SessionLocal", FakeSession)
    monkeypatch.setattr("app.workers.acquisition.RegisteredAcquisitionService", FakeService)
    result = run_acquisition(
        source_id=source,
        connector_id=connector,
        run_key="mission-2026-10-09",
        context={"search_terms": ["Technical Product Manager"], "location": "Nigeria"},
    )
    assert result == {
        "run_id": str(expected.run_id),
        "status": "SUCCEEDED",
        "acquired_count": 12,
        "persisted_count": 9,
        "error_category": None,
        "failed_item_count": 0,
        "retry_after_seconds": None,
    }
