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

## Not verified
Production host and supervisor choice; behaviour under real external sources and real
handler cost; live source canaries (need approved sources and credentials); staging runs.
