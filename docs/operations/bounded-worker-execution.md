# DEP-02 — bounded worker execution

This backend-only runner supervises one cycle of an allowlisted existing worker as a subprocess. It is not a scheduler, database lease manager, new queue, or deployment solution. It does **not** establish end-to-end worker recovery.

## Usage

```bash
python -m app.workers.bounded_runner --worker outbox --batch-size 1 --timeout-seconds 120 --termination-grace-seconds 10
```

Use the other allowlisted worker names in `app/workers/bounded_runner.py`, including the source-discovery candidate worker introduced on the current `main` baseline. The document-retention worker runs its existing one-shot entrypoint without `--once`.

The runner launches the worker as a child process without a shell and passes no credentials through the command line. Credentials, if needed, must be supplied through the normal deployment environment. Execution is limited to one cycle and a configurable wall-clock deadline; the default batch size is one. On deadline or supervisor interruption, it attempts graceful process-group termination, then forces termination after a bounded grace period.

## Exit codes

- Child process exit status on normal completion or failure.
- `124` when the execution deadline is exceeded.
- `143` when the supervisor receives SIGTERM.
- `130` on keyboard interruption.

## Security and resilience boundaries

- Only fixed worker modules can be selected. No arbitrary shell commands, Python modules or user-provided executable paths are accepted.
- No DB claims are automatically rolled back by killing the process. Leases, stale-claim detection, retries, duplicate suppression and external side-effect idempotency remain responsibilities of the existing backend and require PostgreSQL integration tests.
- `--once` does **not** guarantee a short operation: an individual acquisition, browser run or AI call may consume the entire timeout.
- The runner is intended for Linux/POSIX deployment. Production process-group and browser-child cleanup are not yet validated on a real host.
- Termination grace must be shorter than the overall deadline, but the actual shutdown may extend beyond the processing deadline by the grace interval.
- Do not enable production jobs, create credentials, change hosting or rely on the runner for operations SLAs until a worker host passes the zero-cost, reliability, sustainability, simplicity and resilience checks.

## Verification and next gates

Local isolated unit tests and compilation validate process-command construction and deadline handling. Full repository tests, Postgres recovery/concurrency tests, worker-specific runtime tests, deployment packaging and CI must be run and recorded separately against the exact pushed commit. This work is **not** a release authorization.
