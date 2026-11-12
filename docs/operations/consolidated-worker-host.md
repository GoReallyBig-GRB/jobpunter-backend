# Consolidated worker host

`python -m app.workers.worker_host` runs all background workers from **one** supervisor
process. It adds no service, queue, or broker: PostgreSQL work items and the transactional
outbox remain the only coordination. Zero additional recurring cost.

## What it runs
The nine one-cycle workers on the `bounded_runner` allowlist, each as a bounded subprocess
(own process group, hard deadline, process-tree kill). `document_retention_worker` stays a
separately scheduled one-shot job.

| Lane | Workers | Capacity |
|---|---|---|
| reserved | `outbox`, `discovery_scheduler` | always available; never queued behind general work |
| general | mission, run, source refresh, source-candidate, evaluation, requirement extraction, job verification | at most `--general-concurrency` (default 2) at once, longest-waiting first |

## Options
`--once` (one cycle per worker, exit 0 only if all exit 0), `--general-concurrency N`,
`--poll-seconds` (delay between cycles of a worker, default 5), `--worker-timeout-seconds`
(default 300), `--termination-grace-seconds` (default 10), `--batch-size` (default 25),
`--outbox-batch-size` (default 100, runner maximum).

## Shutdown and health
SIGINT/SIGTERM stops every active worker process tree, writes a `STOPPED` heartbeat, exits 0.
A worker over its deadline is terminated (exit recorded as 124) and rescheduled; durable
retry and lease recovery remain the database's job. The host writes a `worker_host` row in
`worker_heartbeats` (active/general counts, cycles, failed cycles) beside each worker's own row.

## Split triggers (move a lane to its own process/service only when)
sustained queue delay; repeated memory pressure; notification latency misses; one workload
using more than ~70% of host capacity.

## Heartbeats and liveness
Each worker subprocess is short-lived, so the host gives it a stable identity
(`JOBPUNTER_HEARTBEAT_INSTANCE_ID`, derived per host run and worker name). Every cycle updates
the same `worker_heartbeats` row; the table does not grow per cycle. Per-worker rows read
`STOPPED` between cycles, which is normal. **Use the `worker_host` row for liveness:** the host
refreshes it every 15 seconds, and `GET /admin/operations/workers` derives `STALE` for a
`RUNNING`/`IDLE` row older than 120 seconds. A crashed or SIGKILLed host therefore turns STALE;
a gracefully stopped host shows `STOPPED`. Alert when `worker_host` is anything other than a
fresh `RUNNING`.

## Overdue-work detection (DEP-04)
`GET /admin/operations/summary` now includes an `overdue` object (read-only counts and ages,
no payloads): `overdue_source_refresh_schedules` and `overdue_discovery_schedules` (active
schedules more than 600 s past `next_run_at`), `oldest_overdue_schedule_seconds`,
`aged_pending_work_items` (PENDING, untouched for more than 900 s), `stuck_processing_work_items`
(PROCESSING for more than 900 s) and `oldest_due_outbox_event_seconds`. Non-zero schedule or
work counts together with a stale `worker_host` heartbeat mean the host or scheduler is down.
Recovery is automatic on restart: due schedules are re-enqueued once per slot (the next run
is re-anchored to now, so a long outage causes no burst of catch-up runs), and expired claims,
leases and outbox locks are reclaimed. Verified on PostgreSQL 17.5 in
`tests/integration/test_schedule_recovery_postgres.py`: a schedule three days overdue yields
exactly one occurrence, three concurrent scheduler polls enqueue it exactly once, and the
detection counts follow added overdue rows. The thresholds are fixed constants, not alert
rules; alerting on them is not implemented.

## Production run contract (hosting not yet chosen)
- Run **one** host instance per environment. A second instance is safe, because claims use
  database locks and execution tokens, but it only adds load.
- Run it under any supervisor that restarts on exit (systemd `Restart=always`, a container
  restart policy, or the platform equivalent). Send SIGTERM to stop; allow at least
  `--termination-grace-seconds` plus 5 seconds before force-killing.
- Provide `DATABASE_URL` and worker credentials through the deployment environment only.
- Schedule `document_retention_worker` separately (for example daily); the host does not run it.
- Restart is safe at any point: leases expire and recovery fails orphaned acquisition runs;
  committed raw records and outbox events stay together.

## Verified (PostgreSQL 17.5, Linux)
- Unit tests: `tests/unit/test_worker_host.py`, `tests/unit/test_worker_heartbeat.py`.
- Real `--once` run: nine workers exit 0, ten heartbeat rows.
- Continuous run with SIGTERM and SIGINT while workers were active: exit 0, no orphan
  processes, `STOPPED` heartbeat.
- Sustained 60 s run, general lane limited to 1 and saturated: reserved workers ran 31 to 34
  cycles with a maximum gap of 2.8 s; each general worker was revisited about every 11 s;
  no failures. Queues were empty, so this shows scheduling behaviour, not data throughput.
- `tests/integration/test_worker_process_tree_linux.py`: a real Chromium tree is gone after
  stop, and a SIGTERM-ignoring grandchild is killed (a leader-only kill leaves it, as a
  negative control).
- `tests/integration/test_worker_kill_during_persistence_postgres.py`: SIGKILL before commit
  leaves zero raw and outbox rows and no held lock; SIGKILL after commit keeps raw and outbox
  together; lease recovery fails the orphaned run.

## Measured capacity (local, PostgreSQL 17.5, empty-handler load)
Seeded 3,000 outbox events (unsupported type, so real claim/lock/update cost and no side
effects) plus 300 pending source-refresh items; host run with `--poll-seconds 0.5
--general-concurrency 3`.
- Default `--batch-size 25`: outbox drained about 7 to 8 events/s (2,400 events in about
  280 s). Each cycle is a new Python process (about 2 s startup), so throughput is bounded by
  batch size divided by cycle time, not by the database.
- `--batch-size 100`: about 28 events/s (1,700 events in 60 s).
- Process tree peak resident memory: about 570 to 590 MB with the host, two reserved workers
  and three general workers (roughly 100 MB per Python worker process). Size the host for this.
- No worker failures, no deadline kills, clean SIGTERM shutdown under load.
Limits: handler cost was near zero, so real notification or acquisition work will be slower;
numbers are from one local machine and are not an SLA.
### Backlog-aware outbox (implemented)
After a clean outbox cycle the host checks (one `EXISTS` query) whether any event is due, and
if so re-runs the outbox immediately instead of waiting `--poll-seconds`. A failed, timed-out
or probe-error cycle always waits the full poll interval, so a broken worker cannot spin.
Only `outbox` is backlog-aware; other workers keep the poll cadence. The outbox also has its
own `--outbox-batch-size` (default 100) because per-event cost is small and each cycle pays
process startup.
Measured (6,000 seeded events, 60 s, `--general-concurrency 3`): new defaults drained about
53 events/s, versus about 7 to 8 events/s for the earlier behaviour (batch 25, poll 0.5 s;
at the default 5 s poll the earlier behaviour would have been lower, about 3 to 4 events/s by
calculation, not measured). During the run the scheduler and general workers kept their
normal cadence and no worker failed. Handler cost was near zero, so real throughput will be lower.

## Idle cost
Workers are started only when a cheap probe (`app/workers/work_probes.py`) says work may exist, or
every `--safety-net-seconds` (default 120) regardless. Measured idle: about 0.08 core at the 120 s default (about 9 CPU-s per two
minutes) versus about 1.5 cores before; see `backend-complexity-review.md`. `--no-work-probe` restores
the old always-start behaviour. Work waits at most one poll (5 s) once a probe sees it; unprobed
`job_verification_worker` waits up to the safety net.

## Not verified
Production host and supervisor choice; behaviour under real external sources and real
handler cost; live source canaries (need approved sources and credentials); staging runs.

## Failure simulations (DEP-07)
`tests/integration/test_worker_host_failure_postgres.py` runs the real `python -m app.workers.worker_host`
against PostgreSQL with real durable outbox work (WORK_REQUESTED signals for DISCOVERY_MISSION items, which
the dispatcher acknowledges without side effects). Results on 2026-11-09 (PostgreSQL 17.5, one local run):

| Scenario | Injected failure | Observed |
|---|---|---|
| Two hosts at once | Two hosts share one outbox of 120 events | All PROCESSED, every event claimed exactly once (`attempt_count == 1`) |
| Host death with backlog | Host SIGKILLed after partial progress of 400 events, then a new host started | All PROCESSED, no event lost, at most 2 claims per event |
| Database outage | Connections to the database refused for 4 s and all sessions terminated mid-run | Host stayed alive, logged heartbeat/worker connection errors, drained all 150 events after recovery, exit 0 |
| Hung worker | Table lock held so the outbox worker's first query blocks | Host killed the worker at its deadline (`worker_deadline_exceeded name=outbox`), stayed alive, drained work after the lock cleared |

Not covered: the probe-error and deadline paths with fake processes are in `tests/unit/test_worker_host.py`;
no real power loss, network partition to a remote database, or host out-of-memory kill was simulated.

Observations (verified by running the host): during an outage the host logs a full traceback per failed
heartbeat and worker start (noisy but harmless).

Hard-killed host: before the fix, a SIGKILLed host left its workers running without the host's deadline (5
children alive 5 s after the host died, 3 still running 8 s after the blocking lock was released). On Linux
each worker now starts with a parent-death signal (`prctl(PR_SET_PDEATHSIG, SIGTERM)` in
`bounded_runner._die_with_parent`), so workers terminate when the host dies. The test
`test_workers_terminate_when_the_host_is_hard_killed` fails without the fix and passes with it. Limits: Linux
only (Windows and macOS hosts are unchanged); it signals the direct worker, so a worker that launches its
own children must clean them up on SIGTERM; the signal is delivered when the thread that started the worker
exits, which for the host is the main thread. Supervisors should still kill the whole process group/cgroup
(systemd `KillMode=control-group` does by default).

## Low-duty idle mode (Neon Free compute budget)

Neon Free compute suspends after 5 idle minutes and each database touch keeps it awake about 5
minutes, so a host that polls every few seconds pins the database awake around the clock. Idle mode
batches database touches when the host has found no work for a while.

Flags (both default to off, which keeps the legacy cadence exactly):
- `--idle-after-seconds N`: no work found for N seconds puts the host into idle mode.
- `--idle-poll-seconds M`: while idle, every worker is polled and the safety net probes at most every
  M seconds (suggested 900, about 34% compute duty, roughly 60 of the 100 free CU-hours/month).

Behaviour: the first cycle that finds work leaves idle mode and pulls every worker's next run forward
so nothing waits out the long interval. Heartbeats are written only at wake-ups, status `IDLE`, with
`idle_mode` and `idle_poll_seconds` metrics. Admin operations treat such a heartbeat as stale only
after `max(stale_after_seconds, 2 * idle_poll_seconds + 30)`; rows without the metric are unchanged.

Trade-off: new work that appears while idle waits up to `idle_poll_seconds` before it is noticed.
Verified by unit tests (`tests/unit/test_worker_host_idle_mode.py`) and an integration test of the
staleness rule. Not yet verified: real compute-hours consumption on Neon.
