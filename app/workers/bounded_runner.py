"""Execute one existing worker cycle with an enforced wall-clock deadline.

Provider independent. This runner supervises a *process*, not a database transaction:
lease expiry, retries, and external-side-effect idempotency must be verified separately.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
from dataclasses import dataclass

logger = logging.getLogger("jobpunter.bounded_runner")

# Explicit allowlist. Do not accept arbitrary module names or shell commands from callers.
BATCH_WORKERS: dict[str, str] = {
    "discovery_scheduler": "app.workers.discovery_scheduler",
    "discovery_mission_worker": "app.workers.discovery_mission_worker",
    "discovery_run_worker": "app.workers.discovery_run_worker",
    "source_refresh_worker": "app.workers.source_refresh_worker",
    "source_discovery_candidate_worker": "app.workers.source_discovery_candidate_worker",
    "discovery_evaluation_worker": "app.workers.discovery_evaluation_worker",
    "job_requirement_extraction_worker": "app.workers.job_requirement_extraction_worker",
    "job_verification_worker": "app.workers.job_verification_worker",
    "outbox": "app.workers.outbox",
}
ONE_SHOT_WORKERS: dict[str, str] = {
    "document_retention_worker": "app.workers.document_retention_worker",
}
WORKERS = {**BATCH_WORKERS, **ONE_SHOT_WORKERS}


@dataclass(frozen=True)
class ExecutionOptions:
    worker: str
    timeout_seconds: float
    termination_grace_seconds: float = 10.0
    batch_size: int = 1

    def validate(self) -> None:
        if self.worker not in WORKERS:
            raise ValueError("worker is not on the execution allowlist")
        if not 1 <= self.batch_size <= 100:
            raise ValueError("batch_size must be from 1 to 100")
        if not 1 <= self.timeout_seconds <= 3600:
            raise ValueError("timeout_seconds must be from 1 to 3600")
        if not 0.1 <= self.termination_grace_seconds <= 60:
            raise ValueError("termination_grace_seconds must be from 0.1 to 60")
        if self.termination_grace_seconds >= self.timeout_seconds:
            raise ValueError("termination grace must be shorter than the deadline")


def _command(options: ExecutionOptions) -> list[str]:
    options.validate()
    command = [sys.executable, "-m", WORKERS[options.worker]]
    if options.worker in BATCH_WORKERS:
        command.extend(["--once", "--batch-size", str(options.batch_size)])
    return command


def _signal_group(process: subprocess.Popen[bytes], signum: int) -> None:
    # POSIX descendants can outlive the immediate child. The process group may
    # still exist even when the group leader has already exited.
    if os.name != "posix" and process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signum)
        elif signum == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:
        pass  # The group or child already exited.


def _taskkill_tree(process: subprocess.Popen[bytes], *, force: bool) -> None:
    command = ["taskkill", "/PID", str(process.pid), "/T"]
    if force:
        command.append("/F")
    subprocess.run(
        command,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_process(process: subprocess.Popen[bytes], grace_seconds: float) -> None:
    if os.name == "nt":
        # taskkill /T targets the full worker tree while the parent PID still
        # identifies its descendants. Windows has no SIGTERM-equivalent that
        # reliably reaches console-process descendants, so force the tree now.
        _taskkill_tree(process, force=True)
    else:
        _signal_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # Enforce the hard deadline for child processes too (e.g. Chromium).
        # Do not skip this just because the Python worker leader has exited.
        if os.name == "nt":
            _taskkill_tree(process, force=True)
        else:
            _signal_group(process, signal.SIGKILL)
        process.wait()  # Reap the child; do not leave a zombie.


def _popen_options() -> dict[str, object]:
    if os.name == "posix":
        return {"start_new_session": True}
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {}


def _die_with_parent() -> None:
    """Linux only, runs in the child before exec: SIGTERM this worker if the host dies.

    Without it a hard-killed host (SIGKILL, OOM kill) leaves its workers running with no
    deadline. Best effort: any failure is ignored and the worker simply starts as before.
    """
    try:
        import ctypes

        pr_set_pdeathsig = 1
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(pr_set_pdeathsig, signal.SIGTERM, 0, 0, 0) != 0:
            return
        if os.getppid() == 1:  # The host already died before the flag took effect.
            os.kill(os.getpid(), signal.SIGTERM)
    except Exception:
        return


def start_process(
    options: ExecutionOptions, env: dict[str, str] | None = None
) -> subprocess.Popen[bytes]:
    """Start one allowlisted worker cycle in its own process group/session.

    Used by the consolidated worker host, which enforces the deadline itself and must
    stop the tree with ``stop_process``. On Linux the worker is also told to terminate if
    the host process dies without cleaning up.
    """
    extra: dict[str, object] = {}
    if sys.platform.startswith("linux"):
        extra["preexec_fn"] = _die_with_parent
    return subprocess.Popen(_command(options), env=env, **_popen_options(), **extra)


def stop_process(process: subprocess.Popen[bytes], grace_seconds: float) -> None:
    """Terminate a worker process tree, escalating to a forced kill, then reap it."""
    _stop_process(process, grace_seconds)


def execute(options: ExecutionOptions) -> int:
    """Return child exit code, 124 for deadline, or 143 for supervisor SIGTERM.

    The batch is not retried within this invocation. Durable retry is the DB's job.
    """
    command = _command(options)
    logger.info("worker_start name=%s batch_size=%d", options.worker, options.batch_size)
    process = subprocess.Popen(command, **_popen_options())
    terminated = False
    previous_term = None

    def _on_term(_signum: int, _frame: object) -> None:
        nonlocal terminated
        terminated = True
        raise InterruptedError("supervisor received SIGTERM")

    try:
        if hasattr(signal, "SIGTERM"):
            previous_term = signal.signal(signal.SIGTERM, _on_term)
        try:
            result = process.wait(timeout=options.timeout_seconds)
            logger.info("worker_exit name=%s exit_code=%s", options.worker, result)
            return result
        except subprocess.TimeoutExpired:
            logger.warning("worker_deadline_exceeded name=%s", options.worker)
            _stop_process(process, options.termination_grace_seconds)
            return 124
        except (InterruptedError, KeyboardInterrupt):
            logger.warning("worker_supervisor_interrupted name=%s", options.worker)
            _stop_process(process, options.termination_grace_seconds)
            return 143 if terminated else 130
    finally:
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", required=True, choices=sorted(WORKERS))
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--termination-grace-seconds", type=float, default=10.0)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    options = ExecutionOptions(
        worker=args.worker,
        timeout_seconds=args.timeout_seconds,
        termination_grace_seconds=args.termination_grace_seconds,
        batch_size=args.batch_size,
    )
    try:
        return execute(options)
    except ValueError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
