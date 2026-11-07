# Backend complexity reduction review

Status: review only. **No change described under "Options" is implemented.** Measurements are from
one local machine (2 cores, PostgreSQL 17.5, Linux) at branch `dep-02/bounded-worker-execution`.

## Question
Is the backend operationally more complex than a small app needs, and what can be simplified
without losing capability? Principle used: keep domain complexity that protects correctness;
remove operational complexity that has no measured benefit.

## Inventory (from the code)
- Ten worker entry points in `app/workers/` (about 3,100 lines): outbox, discovery scheduler, discovery
  mission, discovery run, source refresh, source-discovery candidate, discovery evaluation, requirement
  extraction, job verification, and a one-shot document retention job.
- Each continuous worker polls PostgreSQL at its own interval: 2 s (outbox, mission, run, candidate),
  5 s (evaluation, refresh), 10 s (extraction), 15 s (scheduler), 30 s (verification).
- The scheduler does four jobs per cycle: candidate missions, admin discovery runs, source refreshes,
  and (every 5 min) alert evaluation.
- Claim/lease logic is implemented separately in at least five workers (mission, run, refresh,
  candidate, extraction), each with its own lease constant and stale-claim query. Only source refresh
  uses an execution token for fencing; acquisition writes are fenced in the repository.
- Work requests travel two ways: a `WORK_REQUESTED` outbox event, and workers also poll `work_items`
  directly (`DISCOVERY_MISSION` events are acknowledged by the outbox without execution).

## Measured cost of the consolidated host (defaults)
| Measurement | Result |
|---|---|
| Idle, 60 s, defaults (`--poll-seconds 5`) | 82 worker processes started; about 88 CPU-seconds used on a 2-core machine (about 1.5 cores busy while idle) |
| Cost per started worker process | about 1.07 CPU-seconds; Python import alone is about 0.6 s wall (SQLAlchemy 0.18 s, models 0.21 s) |
| Peak memory, host plus 5 workers | about 570 to 590 MB |
| Outbox drain with backlog-aware re-run, batch 100 | about 53 events/s (handler cost near zero) |

Conclusion: the host is correct and isolates failures, but a process-per-cycle design spends most of
its CPU on starting Python. A free host with a fraction of a core or about 512 MB would not sustain it.
This is a defect of my consolidation design, not of the underlying workers, and it was not visible in
earlier tests because they measured correctness and throughput, not idle cost.

## Classification
**Necessary (keep):** durable queue in PostgreSQL; transactional outbox; leases with expiry and
recovery; acquisition-run fencing; retry ceilings; idempotency keys per schedule slot; process-tree
cleanup for browser work; separate admin visibility (heartbeats, overdue detection).

**Unnecessary or unproven (candidates):**
1. Idle process spawning in the host (measured above). Highest impact.
2. Five hand-rolled claim/lease implementations. Real maintenance risk (the crash-retry and fencing
   defects fixed on this branch were in these copies); no measured runtime cost.
3. Fixed-interval polling with no idle back-off. Likely small DB load; not measured here.
4. Outbox plus direct work-item polling for the same work. Possibly redundant; needs a design review
   of why both exist before touching it.

## Options (none implemented)
- **A. Keep as is.** Simple to reason about; idle cost too high for a zero-cost host.
- **B. Spawn on demand.** The host runs one cheap "is there due work?" query per worker (single
  long-lived connection in the host process) and starts a subprocess only when work exists, plus a
  safety-net spawn per worker every 60 s so a wrong probe can delay but never strand work. Reuses the
  backlog-probe pattern already added for the outbox. Keeps process isolation and deadlines. Risk: probe
  conditions duplicate each worker's claim query and can drift; the safety net bounds the harm. Expected
  idle cost: close to zero spawns when queues are empty (to be measured).
- **C. Hybrid in-process.** Run DB-bound pollers (outbox, scheduler, mission, run, candidate,
  evaluation, extraction, verification) as long-lived loops in the host process, and keep subprocesses
  only for acquisition work that can launch browsers (source refresh). Lowest steady CPU and memory;
  largest change; a stuck in-process loop is harder to kill than a subprocess.
- **D. Shared claim helper.** Replace the five claim implementations with one tested helper. Real
  correctness benefit; high regression risk across discovery workflows; no performance benefit.

## Recommendation
1. Do **B** first. Smallest change that addresses the measured problem. Acceptance: idle spawns per
   minute and idle CPU measured before and after; every worker still drains a seeded backlog; all
   existing PostgreSQL tests pass; a deliberately wrong probe still gets work done via the safety net.
2. Re-measure. Move to **C** only if B does not fit the chosen host's CPU and memory limits.
3. Treat **D** as a separate, later hardening task with its own tests; do not bundle it with B.
4. Do not merge the outbox and work-item paths until their relationship is documented.

## Not verified
Host CPU and memory quotas (depends on DEP-01 provider choice); database load from idle polling;
behaviour of option B or C (not built); whether probes can mirror every worker's claim rules exactly.
Any option needs your authorization before implementation, because it changes working code.
