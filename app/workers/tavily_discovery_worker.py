"""Run one budgeted Tavily company-discovery step (system-initiated; never triggered by a candidate)."""
from __future__ import annotations

import argparse
import logging

from app.application.jobs.tavily_discovery import run_discovery_step
from app.application.operations.worker_heartbeat import build_worker_heartbeat
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.search import tavily

logger = logging.getLogger("jobpunter.tavily_discovery_worker")


def run_once() -> dict:
    if tavily.api_key() is None:
        return {"status": "DISABLED_NO_API_KEY"}
    with SessionLocal() as db:
        result = run_discovery_step(db)
    return {
        "status": result.status, "outcome": result.outcome, "credits": result.credits,
        "urls": result.urls, "new_boards": result.new_boards,
        "probe_ok": result.probe_ok, "rejected": result.rejected,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--batch-size", type=int, default=1)  # accepted for runner compatibility; one step per run
    parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    heartbeat = build_worker_heartbeat("tavily_discovery_worker")
    heartbeat("RUNNING", {})
    try:
        outcome = run_once()
    except Exception as exc:
        heartbeat("FAILED", {}, type(exc).__name__)
        raise
    logger.info("tavily discovery step %s", outcome)
    heartbeat("IDLE", outcome, successful_cycle=True)
    heartbeat("STOPPED", outcome)


if __name__ == "__main__":
    main()
