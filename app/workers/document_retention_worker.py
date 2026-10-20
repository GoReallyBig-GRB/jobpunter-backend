"""Scheduled entrypoint: python -m app.workers.document_retention_worker."""
import json

from app.application.documents.retention import expire_eligible_documents
from app.application.operations.worker_heartbeat import build_worker_heartbeat
from app.infrastructure.db.session import SessionLocal


def main() -> None:
    heartbeat = build_worker_heartbeat("document_retention_worker")
    heartbeat("RUNNING")
    try:
        with SessionLocal() as db:
            result = expire_eligible_documents(db)
    except Exception as exc:
        heartbeat("FAILED", error_category=type(exc).__name__)
        raise
    heartbeat("STOPPED", result if isinstance(result, dict) else None, successful_cycle=True)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
