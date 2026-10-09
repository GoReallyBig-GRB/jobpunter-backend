"""Explicitly invoke one persisted source connector from an operator-controlled process."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from uuid import UUID

from app.application.jobs.registered_acquisition import RegisteredAcquisitionService
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
from app.infrastructure.db.session import SessionLocal

logger = logging.getLogger("jobpunter.acquisition_worker")


def run_acquisition(
    *, source_id: UUID, connector_id: UUID, run_key: str, context: dict
) -> dict:
    """Run a registered connector and return stable, log-safe result fields."""
    with SessionLocal() as db:
        service = RegisteredAcquisitionService(SqlAlchemyAcquisitionRepository(db))
        result = service.run(source_id, connector_id, run_key, context)
        return {
            "run_id": str(result.run_id),
            "status": result.status,
            "acquired_count": result.acquired_count,
            "persisted_count": result.persisted_count,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", required=True, type=UUID)
    parser.add_argument("--connector-id", required=True, type=UUID)
    parser.add_argument("--run-key", required=True, help="stable idempotency key for this acquisition")
    parser.add_argument("--context-file", required=True, type=Path, help="JSON file with connector-specific search settings")
    args = parser.parse_args()

    context = json.loads(args.context_file.read_text(encoding="utf-8"))
    if not isinstance(context, dict):
        parser.error("context file must contain a JSON object")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_acquisition(
        source_id=args.source_id,
        connector_id=args.connector_id,
        run_key=args.run_key,
        context=context,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
