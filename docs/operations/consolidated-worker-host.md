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
(default 300), `--termination-grace-seconds` (default 10), `--batch-size` (default 25).

## Shutdown and health
SIGINT/SIGTERM stops every active worker process tree, writes a `STOPPED` heartbeat, exits 0.
A worker over its deadline is terminated (exit recorded as 124) and rescheduled; durable
retry and lease recovery remain the database's job. The host writes a `worker_host` row in
`worker_heartbeats` (active/general counts, cycles, failed cycles) beside each worker's own row.

## Split triggers (move a lane to its own process/service only when)
sustained queue delay; repeated memory pressure; notification latency misses; one workload
using more than ~70% of host capacity.

## Verified
Unit tests (`tests/unit/test_worker_host.py`); real `--once` and continuous SIGTERM/SIGINT runs
against a migrated PostgreSQL 17.5. Not verified: Linux Chromium/browser child cleanup under
load, forced kill during persistence, live source canaries, production deployment topology.
