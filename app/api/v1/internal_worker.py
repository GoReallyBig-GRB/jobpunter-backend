"""Protected trigger for bounded worker cycles (see app.application.operations.worker_trigger)."""

from __future__ import annotations

import hmac
from functools import lru_cache

from fastapi import APIRouter, Header, HTTPException

from app.application.operations.worker_trigger import TriggerLimits, WorkerCycleRunner
from app.config.settings import get_settings

router = APIRouter(prefix="/internal", tags=["internal"])


@lru_cache
def get_runner() -> WorkerCycleRunner:
    s = get_settings()
    return WorkerCycleRunner(
        TriggerLimits(
            batch_size=s.worker_trigger_batch_size,
            worker_timeout_seconds=s.worker_trigger_worker_timeout_seconds,
            total_seconds=s.worker_trigger_total_seconds,
            min_free_mb=s.worker_trigger_min_free_mb,
        )
    )


def _authorize(provided: str | None) -> None:
    secret = get_settings().worker_trigger_secret
    if not secret:
        # Not configured: behave as if the route does not exist.
        raise HTTPException(status_code=404, detail="Not Found")
    if not provided or not hmac.compare_digest(provided.encode(), secret.encode()):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/worker-cycle", status_code=202)
def start_worker_cycle(x_worker_trigger_secret: str | None = Header(default=None)) -> dict:
    _authorize(x_worker_trigger_secret)
    outcome, run_id = get_runner().start()
    if outcome == "BUSY":
        raise HTTPException(status_code=409, detail="A worker cycle is already running")
    if outcome == "LOW_MEMORY":
        raise HTTPException(status_code=503, detail="Not enough free memory to start a worker cycle")
    return {"status": "STARTED", "run_id": run_id}


@router.get("/worker-cycle")
def worker_cycle_status(x_worker_trigger_secret: str | None = Header(default=None)) -> dict:
    _authorize(x_worker_trigger_secret)
    return get_runner().status()
