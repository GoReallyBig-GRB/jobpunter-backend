"""Run candidate evaluation for missions whose acquisition waves have completed."""
from __future__ import annotations

import argparse
import logging
import signal
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import UUID

from sqlalchemy import select

from app.application.jobs.discovery_evaluation import evaluate_discovery_mission
from app.infrastructure.ai.requirement_matcher import configured_requirement_matcher
from app.infrastructure.db.models import DiscoveryMission
from app.infrastructure.db.session import SessionLocal

logger = logging.getLogger("jobpunter.discovery_evaluation_worker")


class DiscoveryEvaluationWorker:
    def __init__(self, session_factory=SessionLocal, matcher=None):
        self.session_factory = session_factory
        # None is an intentional safe mode: missions remain NEEDS_REVIEW until configured.
        self.matcher = matcher if matcher is not None else configured_requirement_matcher()

    def run_once(self, limit: int = 10) -> dict[str, int]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self.session_factory() as db:
            now = datetime.now(timezone.utc)
            stale_before = now - timedelta(minutes=5)
            missions = db.scalars(
                select(DiscoveryMission)
                .where(
                    (DiscoveryMission.status == "AWAITING_EVALUATION")
                    | ((DiscoveryMission.status == "EVALUATING") & (DiscoveryMission.updated_at <= stale_before))
                )
                .order_by(DiscoveryMission.created_at, DiscoveryMission.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
            mission_ids = [mission.id for mission in missions]
            for mission in missions:
                mission.status = "EVALUATING"
                mission.updated_at = now
            db.commit()
        outcome = {"claimed": len(mission_ids), "completed": 0, "failed": 0}
        for mission_id in mission_ids:
            try:
                with self.session_factory() as db:
                    result = evaluate_discovery_mission(db, mission_id, matcher=self.matcher)
                logger.info(
                    "discovery evaluation mission=%s status=%s evaluated=%s usable=%s",
                    mission_id, result["status"], result.get("jobs_evaluated", 0),
                    result.get("usable_opportunities", 0),
                )
                outcome["completed"] += 1
            except Exception as exc:
                # Do not emit traceback text: provider/database exceptions can contain private data.
                logger.error(
                    "discovery evaluation failed mission=%s error_type=%s",
                    mission_id, type(exc).__name__,
                )
                with self.session_factory() as db:
                    mission = db.get(DiscoveryMission, mission_id)
                    if mission is not None and mission.status == "EVALUATING":
                        mission.error_code = type(exc).__name__
                        mission.updated_at = datetime.now(timezone.utc)
                        db.commit()
                outcome["failed"] += 1
        return outcome


def run_worker(
    *, interval_seconds: float = 5.0, batch_size: int = 10, once: bool = False,
    should_stop: Callable[[], bool] = lambda: False, sleep: Callable[[float], None] = time.sleep,
    worker: DiscoveryEvaluationWorker | None = None,
) -> int:
    if interval_seconds <= 0 or batch_size < 1:
        raise ValueError("interval_seconds and batch_size must be positive")
    consumer = worker or DiscoveryEvaluationWorker()
    cycles = 0
    while not should_stop():
        outcome = consumer.run_once(batch_size)
        cycles += 1
        logger.info("discovery evaluation cycle=%s outcome=%s", cycles, outcome)
        if once:
            break
        if outcome["claimed"] == 0:
            sleep(interval_seconds)
    return cycles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--interval-seconds", type=float, default=5.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stopped = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    run_worker(
        interval_seconds=args.interval_seconds,
        batch_size=args.batch_size,
        once=args.once,
        should_stop=lambda: stopped,
    )


if __name__ == "__main__":
    main()
