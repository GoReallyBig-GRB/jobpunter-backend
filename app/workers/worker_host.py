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
    last_start: float | None = None
    skipped_idle: int = 0
    # True while the running cycle was started because work may exist (not by the safety net).
    for_work: bool = False


@dataclass(frozen=True)
class HostConfig:
    general_concurrency: int = 2
    poll_seconds: float = 5.0
    worker_timeout_seconds: float = 300.0
    termination_grace_seconds: float = 10.0
    batch_size: int = 25
    # Outbox events are cheap to dispatch; each cycle pays process startup, so use a larger batch.
    outbox_batch_size: int = 100
    # With a work probe, a worker with no apparent work is still started at least this often.
    safety_net_seconds: float = 120.0
    # Low-duty idle mode (None = disabled). After ``idle_after_seconds`` with no work found, every
    # probe, safety-net start and heartbeat is batched into one wake-up per ``idle_poll_seconds``, so
    # a scale-to-zero database can suspend between wake-ups. New work waits for the next wake-up.
    idle_poll_seconds: float | None = None
    idle_after_seconds: float = 300.0

    def validate(self) -> None:
        if not 1 <= self.general_concurrency <= len(GENERAL_WORKERS):
            raise ValueError(f"general_concurrency must be 1..{len(GENERAL_WORKERS)}")
        if not 0.05 <= self.poll_seconds <= 3600:
            raise ValueError("poll_seconds must be from 0.05 to 3600")
        if not self.poll_seconds <= self.safety_net_seconds <= 86400:
            raise ValueError("safety_net_seconds must be from poll_seconds to 86400")
        if self.idle_poll_seconds is not None and not (
            self.poll_seconds <= self.idle_poll_seconds <= 3600
        ):
            raise ValueError("idle_poll_seconds must be from poll_seconds to 3600")
        if not 0 <= self.idle_after_seconds <= 86400:
            raise ValueError("idle_after_seconds must be from 0 to 86400")
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
    # work_probe(name) -> True when the worker may have work. None = always start (legacy).
    work_probe: Callable[[str], bool] | None = None
    clock: Callable[[], float] = time.monotonic
    slots: dict[str, _Slot] = field(init=False)

    def __post_init__(self) -> None:
        self.config.validate()
        self._host_id: UUID = uuid4()
        if self.spawn is None:
            self.spawn = self._spawn_with_stable_heartbeat
        self.slots = {n: _Slot(n, True) for n in RESERVED_WORKERS}
        self.slots.update({n: _Slot(n, False) for n in GENERAL_WORKERS})
        self._idle_since: float | None = None
        self._idle_next_wake: float | None = None
        self._woke = False
        self._work_started = False

    def _idle_mode(self, now: float) -> bool:
        return (
            self.config.idle_poll_seconds is not None
            and self._idle_since is not None
            and now - self._idle_since >= self.config.idle_after_seconds
        )

    def _poll_delay(self, now: float) -> float:
        """Seconds until the next probe: ``poll_seconds``, or the shared idle wake-up while idle.

        All slots share one idle wake-up time so their database touches cluster into one burst.
        """
        if not self._idle_mode(now):
            self._idle_next_wake = None
            return self.config.poll_seconds
        assert self.config.idle_poll_seconds is not None
        if self._idle_next_wake is None or now >= self._idle_next_wake:
            self._idle_next_wake = now + self.config.idle_poll_seconds
        return self._idle_next_wake - now
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

    def _safety_interval(self, now: float) -> float:
        """Safety-net interval; stretched while idle so wake-ups do not respawn every worker."""
        if self._idle_mode(now):
            assert self.config.idle_poll_seconds is not None
            return max(self.config.safety_net_seconds, 4 * self.config.idle_poll_seconds)
        return self.config.safety_net_seconds

    def _start_reason(self, slot: _Slot, now: float) -> str | None:
        """Why to start now: "always", "work", "safety" (interval elapsed), or None to skip.

        Probe-gated start: work may exist, or the safety-net interval elapsed. Fails open.
        """
        if self.work_probe is None:
            return "always"
        if slot.last_start is None:
            return "safety"  # first cycle: establish a baseline
        try:
            if self.work_probe(slot.name):
                return "work"
        except Exception:  # A broken probe must never strand work.
            logger.exception("work_probe_failed name=%s", slot.name)
            return "work"
        if now - slot.last_start >= self._safety_interval(now):
            return "safety"
        return None

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
            slot.for_work = False
            slot.runs += 1
            slot.last_exit = code
            if code != 0:
                slot.failures += 1
                logger.warning("worker_failed name=%s exit_code=%s", slot.name, code)
            # Immediate re-run only after a clean cycle; failures always wait a full poll.
            if code == 0 and self._backlog_remains(slot.name):
                slot.next_due = now
            else:
                slot.next_due = now + self._poll_delay(now)

    def _start_due(self, now: float, *, once_pending: set[str] | None = None) -> None:
        general_running = sum(1 for s in self.active() if not s.reserved)
        # Reserved workers first, then the longest-waiting general workers (fairness).
        for slot in sorted(self.slots.values(), key=lambda s: (not s.reserved, s.next_due)):
            if slot.process is not None or slot.next_due > now:
                continue
            if once_pending is not None and slot.name not in once_pending:
                continue
            if not slot.reserved and general_running >= self.config.general_concurrency:
                continue
            reason = "once" if once_pending is not None else self._start_reason(slot, now)
            if self._idle_mode(now):
                self._woke = True
            if reason is None:
                slot.skipped_idle += 1
                slot.next_due = now + self._poll_delay(now)
                continue
            if not slot.reserved:
                general_running += 1
            slot.for_work = reason in ("always", "work", "once")
            self._work_started = self._work_started or slot.for_work
            slot.process = self.spawn(self._options(slot.name))
            slot.last_start = now
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
                "idle_skips": sum(s.skipped_idle for s in self.slots.values()),
                "idle_mode": self._idle_mode(self.clock()),
                **(
                    {"idle_poll_seconds": self.config.idle_poll_seconds}
                    if self.config.idle_poll_seconds is not None
                    else {}
                ),
            },
            error,
            ok,
        )

    def tick(self, *, once_pending: set[str] | None = None) -> None:
        now = self.clock()
        was_idle = self._idle_mode(now)
        self._reap(now)
        self._work_started = False
        self._start_due(now, once_pending=once_pending)
        if self._work_started or any(s.for_work for s in self.active()):
            self._idle_since = None
            if was_idle:
                # Work appeared: leave idle mode for every slot, not only the one that found work.
                soonest = now + self.config.poll_seconds
                for slot in self.slots.values():
                    if slot.process is None and slot.next_due > soonest:
                        slot.next_due = soonest
        elif self._idle_since is None:
            self._idle_since = now

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
                self._woke = False
                self.tick()
                idle = self._idle_mode(self.clock())
                # While idle, heartbeat only at the shared wake-up so the database can suspend.
                due = self._woke if idle else self.clock() - last_beat >= 15
                if due:
                    self._beat("IDLE" if idle else "RUNNING", ok=True)
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


def _db_work_probe(name: str) -> bool:
    from app.workers.work_probes import db_work_probe

    return db_work_probe(name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="run every worker one cycle and exit")
    parser.add_argument("--general-concurrency", type=int, default=2)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--worker-timeout-seconds", type=float, default=300.0)
    parser.add_argument("--termination-grace-seconds", type=float, default=10.0)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--outbox-batch-size", type=int, default=100)
    parser.add_argument("--safety-net-seconds", type=float, default=120.0)
    parser.add_argument("--idle-poll-seconds", type=float, default=None,
                        help="low-duty idle mode: one batched wake-up this often after a quiet period "
                             "(lets a scale-to-zero database suspend; new work waits for it)")
    parser.add_argument("--idle-after-seconds", type=float, default=300.0,
                        help="quiet time with no work before idle mode starts")
    parser.add_argument("--no-work-probe", action="store_true",
                        help="start every worker each poll (legacy; high idle CPU)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = HostConfig(
        general_concurrency=args.general_concurrency,
        poll_seconds=args.poll_seconds,
        worker_timeout_seconds=args.worker_timeout_seconds,
        termination_grace_seconds=args.termination_grace_seconds,
        batch_size=args.batch_size,
        outbox_batch_size=args.outbox_batch_size,
        safety_net_seconds=args.safety_net_seconds,
        idle_poll_seconds=args.idle_poll_seconds,
        idle_after_seconds=args.idle_after_seconds,
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
        work_probe=None if args.no_work_probe else _db_work_probe,
    )
    if args.once:
        return host.run_once(stopping.is_set)
    return host.run_forever(stopping.is_set)


if __name__ == "__main__":
    raise SystemExit(main())
