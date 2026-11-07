"""Run all JobPunter background workers from ONE supervisor process.

The host starts the existing one-cycle workers as bounded subprocesses (own process group,
hard deadline, process-tree cleanup via ``bounded_runner``). It adds no queue or service:
PostgreSQL work items and the transactional outbox remain the only coordination.

Logical lanes protect latency-sensitive work from heavy acquisition work:
* reserved lane: ``outbox`` and ``discovery_scheduler``; always have capacity.
* general lane: every other worker; at most ``--general-concurrency`` run at once.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID, uuid4, uuid5

from app.application.operations.worker_heartbeat import INSTANCE_ID_ENV
from app.workers.bounded_runner import (
    BATCH_WORKERS,
    ExecutionOptions,
    start_process,
    stop_process,
)

logger = logging.getLogger("jobpunter.worker_host")

RESERVED_WORKERS: tuple[str, ...] = ("outbox", "discovery_scheduler")
GENERAL_WORKERS: tuple[str, ...] = tuple(
    name for name in BATCH_WORKERS if name not in RESERVED_WORKERS
)
HOST_NAME = "worker_host"


@dataclass
class _Slot:
    name: str
    reserved: bool
    next_due: float = 0.0
    process: subprocess.Popen[bytes] | None = None
    deadline: float = 0.0
    runs: int = 0
    failures: int = 0
    last_exit: int | None = None


@dataclass(frozen=True)
class HostConfig:
    general_concurrency: int = 2
    poll_seconds: float = 5.0
    worker_timeout_seconds: float = 300.0
    termination_grace_seconds: float = 10.0
    batch_size: int = 25
    # Outbox events are cheap to dispatch; each cycle pays process startup, so use a larger batch.
    outbox_batch_size: int = 100

    def validate(self) -> None:
        if not 1 <= self.general_concurrency <= len(GENERAL_WORKERS):
            raise ValueError(f"general_concurrency must be 1..{len(GENERAL_WORKERS)}")
        if not 0.05 <= self.poll_seconds <= 3600:
            raise ValueError("poll_seconds must be from 0.05 to 3600")
        # Delegate the remaining bounds to the runner's own validation.
        ExecutionOptions(
            "outbox",
            self.worker_timeout_seconds,
            self.termination_grace_seconds,
            self.batch_size,
        ).validate()
        ExecutionOptions(
            "outbox",
            self.worker_timeout_seconds,
            self.termination_grace_seconds,
            self.outbox_batch_size,
        ).validate()


# Workers whose clean cycle should be followed by an immediate re-run while work remains.
BACKLOG_AWARE_WORKERS: frozenset[str] = frozenset({"outbox"})


@dataclass
class WorkerHost:
    config: HostConfig
    heartbeat: Callable[..., None] | None = None
    spawn: Callable[[ExecutionOptions], subprocess.Popen[bytes]] | None = None
    stop: Callable[[subprocess.Popen[bytes], float], None] = stop_process
    # backlog_probe(name) -> True when more work is already due for that worker.
    backlog_probe: Callable[[str], bool] | None = None
    clock: Callable[[], float] = time.monotonic
    slots: dict[str, _Slot] = field(init=False)

    def __post_init__(self) -> None:
        self.config.validate()
        self._host_id: UUID = uuid4()
        if self.spawn is None:
            self.spawn = self._spawn_with_stable_heartbeat
        self.slots = {n: _Slot(n, True) for n in RESERVED_WORKERS}
        self.slots.update({n: _Slot(n, False) for n in GENERAL_WORKERS})

    def worker_instance_id(self, name: str) -> UUID:
        """Stable per host run, so each worker's heartbeat row is updated in place."""
        return uuid5(self._host_id, name)

    def _spawn_with_stable_heartbeat(self, options: ExecutionOptions) -> subprocess.Popen[bytes]:
        env = {**os.environ, INSTANCE_ID_ENV: str(self.worker_instance_id(options.worker))}
        return start_process(options, env)

    def _options(self, name: str) -> ExecutionOptions:
        c = self.config
        batch = c.outbox_batch_size if name == "outbox" else c.batch_size
        return ExecutionOptions(
            name, c.worker_timeout_seconds, c.termination_grace_seconds, batch
        )

    def _backlog_remains(self, name: str) -> bool:
        if self.backlog_probe is None or name not in BACKLOG_AWARE_WORKERS:
            return False
        try:
            return bool(self.backlog_probe(name))
        except Exception:  # A failing probe must never stop or spin the worker.
            logger.exception("backlog_probe_failed name=%s", name)
            return False

    def active(self) -> list[_Slot]:
        return [s for s in self.slots.values() if s.process is not None]

    def _reap(self, now: float) -> None:
        for slot in self.active():
            process = slot.process
            assert process is not None
            code = process.poll()
            if code is None and now >= slot.deadline:
                logger.warning("worker_deadline_exceeded name=%s", slot.name)
                self.stop(process, self.config.termination_grace_seconds)
                code = 124
            if code is None:
                continue
            slot.process = None
            slot.runs += 1
            slot.last_exit = code
            if code != 0:
                slot.failures += 1
                logger.warning("worker_failed name=%s exit_code=%s", slot.name, code)
            # Immediate re-run only after a clean cycle; failures always wait a full poll.
            if code == 0 and self._backlog_remains(slot.name):
                slot.next_due = now
            else:
                slot.next_due = now + self.config.poll_seconds

    def _start_due(self, now: float, *, once_pending: set[str] | None = None) -> None:
        general_running = sum(1 for s in self.active() if not s.reserved)
        # Reserved workers first, then the longest-waiting general workers (fairness).
        for slot in sorted(self.slots.values(), key=lambda s: (not s.reserved, s.next_due)):
            if slot.process is not None or slot.next_due > now:
                continue
            if once_pending is not None and slot.name not in once_pending:
                continue
            if not slot.reserved:
                if general_running >= self.config.general_concurrency:
                    continue
                general_running += 1
            slot.process = self.spawn(self._options(slot.name))
            slot.deadline = now + self.config.worker_timeout_seconds
            logger.info("worker_start name=%s lane=%s", slot.name,
                        "reserved" if slot.reserved else "general")

    def _beat(self, status: str, error: str | None = None, ok: bool = False) -> None:
        if self.heartbeat is None:
            return
        self.heartbeat(
            status,
            {
                "active_workers": len(self.active()),
                "general_active": sum(1 for s in self.active() if not s.reserved),
                "general_limit": self.config.general_concurrency,
                "cycles": sum(s.runs for s in self.slots.values()),
                "failed_cycles": sum(s.failures for s in self.slots.values()),
            },
            error,
            ok,
        )

    def tick(self, *, once_pending: set[str] | None = None) -> None:
        now = self.clock()
        self._reap(now)
        self._start_due(now, once_pending=once_pending)

    def shutdown(self) -> None:
        """Stop every active worker process tree; leaves no orphaned children."""
        for slot in self.active():
            process = slot.process
            assert process is not None
            logger.warning("worker_host_stopping name=%s", slot.name)
            self.stop(process, self.config.termination_grace_seconds)
            slot.process = None
            slot.last_exit = 143
        self._beat("STOPPED")

    def run_once(self, should_stop: Callable[[], bool] = lambda: False) -> int:
        """Run each worker exactly once under the lane limits; 0 only if all exit 0."""
        pending = set(self.slots)
        self._beat("RUNNING")
        try:
            while pending and not should_stop():
                self.tick(once_pending=pending)
                for slot in self.slots.values():
                    if slot.name in pending and slot.process is None and slot.runs:
                        pending.discard(slot.name)
                if pending:
                    time.sleep(0.05)
            while self.active() and not should_stop():
                self.tick(once_pending=set())
                time.sleep(0.05)
        finally:
            self.shutdown()
        failed = [s.name for s in self.slots.values() if s.last_exit != 0]
        return 1 if failed else 0

    def run_forever(self, should_stop: Callable[[], bool]) -> int:
        self._beat("RUNNING")
        last_beat = self.clock()
        try:
            while not should_stop():
                self.tick()
                if self.clock() - last_beat >= 15:
                    self._beat("RUNNING", ok=True)
                    last_beat = self.clock()
                time.sleep(0.2)
        finally:
            self.shutdown()
        return 0


def _outbox_backlog_probe(name: str) -> bool:
    """True when at least one outbox event is due now (cheap EXISTS query)."""
    from datetime import UTC, datetime

    from sqlalchemy import exists, select

    from app.infrastructure.db.models import OutboxEvent
    from app.infrastructure.db.session import SessionLocal

    if name != "outbox":
        return False
    with SessionLocal() as db:
        return bool(db.scalar(select(exists().where(
            OutboxEvent.status == "PENDING",
            OutboxEvent.available_at <= datetime.now(UTC),
        ))))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="run every worker one cycle and exit")
    parser.add_argument("--general-concurrency", type=int, default=2)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--worker-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--termination-grace-seconds", type=float, default=10.0)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--outbox-batch-size", type=int, default=100)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = HostConfig(
        general_concurrency=args.general_concurrency,
        poll_seconds=args.poll_seconds,
        worker_timeout_seconds=args.worker_timeout_seconds,
        termination_grace_seconds=args.termination_grace_seconds,
        batch_size=args.batch_size,
        outbox_batch_size=args.outbox_batch_size,
    )
    try:
        config.validate()
    except ValueError as exc:
        parser.error(str(exc))

    from app.application.operations.worker_heartbeat import build_worker_heartbeat

    stopping = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    host = WorkerHost(
        config,
        heartbeat=build_worker_heartbeat(HOST_NAME),
        backlog_probe=_outbox_backlog_probe,
    )
    if args.once:
        return host.run_once(stopping.is_set)
    return host.run_forever(stopping.is_set)


if __name__ == "__main__":
    raise SystemExit(main())
