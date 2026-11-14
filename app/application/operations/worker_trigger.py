"""Run one bounded cycle of each light worker on demand, one at a time, inside memory limits.

This exists for hosts that cannot run an always-on worker process (Render Free: 512 MB and
no second always-on service). An external timer calls the protected route; this module runs
the allowlisted workers through ``bounded_runner`` as short-lived child processes. It adds no
queue and no scheduler: durable work, leases, retries and idempotency stay in the database.

Browser-capable orchestration (``discovery_mission_worker``) and the document retention
worker are deliberately not in the sequence. Do not configure JOBSPY or PLAYWRIGHT sources
for a deployment that triggers workers this way: a browser would not fit next to the API.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.workers.bounded_runner import ExecutionOptions, start_process, stop_process

logger = logging.getLogger(__name__)

TRIGGER_WORKERS: tuple[str, ...] = (
    "outbox",
    "discovery_scheduler",
    "source_discovery_candidate_worker",
    "source_refresh_worker",
    "discovery_run_worker",
    "discovery_evaluation_worker",
    "job_verification_worker",
    "job_requirement_extraction_worker",
)
_GRACE_SECONDS = 5.0


@dataclass(frozen=True)
class TriggerLimits:
    batch_size: int
    worker_timeout_seconds: float
    total_seconds: float
    min_free_mb: int


MemoryProbe = Callable[[], float | None]
ProcessStarter = Callable[[ExecutionOptions], object]


def _read_int(path: str) -> int | None:
    try:
        text = Path(path).read_text().strip()
    except OSError:
        return None
    if not text or text == "max":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _inactive_file_bytes(limit_path: str) -> int:
    stat = Path(limit_path).with_name("memory.stat")
    try:
        for line in stat.read_text().splitlines():
            key, _, value = line.partition(" ")
            if key in ("inactive_file", "total_inactive_file"):
                return int(value)
    except (OSError, ValueError):
        pass
    return 0


def available_memory_mb() -> float | None:
    """Free memory for this container in MB, or None when it cannot be measured.

    Uses the container (cgroup) limit when one is set, otherwise the host's MemAvailable.
    """
    host_available: float | None = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                host_available = int(line.split()[1]) / 1024
                break
    except (OSError, ValueError, IndexError):
        host_available = None
    for limit_path, used_path in (
        ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
        ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory/memory.usage_in_bytes"),
    ):
        limit, used = _read_int(limit_path), _read_int(used_path)
        if limit is None or used is None:
            continue
        used -= _inactive_file_bytes(limit_path)  # Reclaimable page cache is not pressure.
        if host_available is not None and limit > host_available * 1024 * 1024 * 4:
            continue  # An effectively unlimited cgroup: trust the host figure instead.
        return max(0.0, (limit - used) / (1024 * 1024))
    return host_available


class WorkerCycleRunner:
    """Single-flight runner. At most one cycle runs at a time in this process."""

    def __init__(
        self,
        limits: TriggerLimits,
        *,
        memory_probe: MemoryProbe = available_memory_mb,
        starter: ProcessStarter = start_process,
        stopper: Callable[[object, float], None] = stop_process,
        workers: tuple[str, ...] = TRIGGER_WORKERS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits
        self._memory_probe = memory_probe
        self._starter = starter
        self._stopper = stopper
        self._workers = workers
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict = {"state": "NEVER_RUN"}
        self._thread: threading.Thread | None = None

    def memory_ok(self) -> bool:
        free = self._memory_probe()
        return free is not None and free >= self.limits.min_free_mb

    def status(self) -> dict:
        free = self._memory_probe()
        with self._lock:
            return {**self._state, "free_mb": None if free is None else round(free)}

    def start(self) -> tuple[str, str | None]:
        """Return ("STARTED", run_id), ("BUSY", None) or ("LOW_MEMORY", None)."""
        if not self.memory_ok():
            return "LOW_MEMORY", None
        run_id = str(uuid4())
        with self._lock:
            if self._state.get("state") == "RUNNING":
                return "BUSY", None
            self._state = {
                "state": "RUNNING",
                "run_id": run_id,
                "started_at": datetime.now(UTC).isoformat(),
                "results": [],
            }
        self._thread = threading.Thread(target=self._run, args=(run_id,), daemon=True)
        self._thread.start()
        return "STARTED", run_id

    def _record(self, result: dict) -> None:
        with self._lock:
            self._state["results"].append(result)

    def _run(self, run_id: str) -> None:
        deadline = self._clock() + self.limits.total_seconds
        try:
            for name in self._workers:
                remaining = deadline - self._clock()
                if remaining < 2:
                    self._record({"worker": name, "status": "SKIPPED_DEADLINE"})
                    continue
                if not self.memory_ok():
                    self._record({"worker": name, "status": "SKIPPED_LOW_MEMORY"})
                    continue
                self._record(self._run_one(name, min(self.limits.worker_timeout_seconds, remaining)))
        except Exception:  # Never let the thread die silently; report a safe category only.
            logger.exception("worker_trigger_cycle_failed")
            self._record({"worker": "-", "status": "TRIGGER_ERROR"})
        finally:
            with self._lock:
                self._state["state"] = "FINISHED"
                self._state["finished_at"] = datetime.now(UTC).isoformat()

    def _run_one(self, name: str, timeout: float) -> dict:
        grace = min(_GRACE_SECONDS, max(0.2, timeout / 2))
        options = ExecutionOptions(
            worker=name,
            timeout_seconds=max(timeout, grace + 0.5),
            termination_grace_seconds=grace,
            batch_size=self.limits.batch_size,
        )
        started = self._clock()
        try:
            process = self._starter(options)
        except Exception:
            logger.exception("worker_trigger_start_failed name=%s", name)
            return {"worker": name, "status": "START_FAILED"}
        try:
            code = process.wait(timeout=options.timeout_seconds)  # type: ignore[attr-defined]
            status = "OK" if code == 0 else "FAILED"
        except subprocess.TimeoutExpired:
            self._stopper(process, grace)
            code, status = 124, "TIMEOUT"
        return {
            "worker": name,
            "status": status,
            "exit_code": code,
            "seconds": round(self._clock() - started, 1),
        }
