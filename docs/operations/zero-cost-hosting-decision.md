# Zero-cost hosting and database decision (DEP-01 / DEP-05 / DEP-06 input)

Status: PROVISIONAL (owner direction, 2026-11-09): Neon Free is the provisional DEVELOPMENT database, Aiven free is the
fallback. Not accepted for production; the Python worker host is still unresolved, and the constraint in "DEP-01
findings (2026-11-09)" below applies. Nothing here is deployed. Rule from the product owner: every connection and the
hosting must be zero-cost with no card; a "free tier" that asks for a card is excluded.

## What the backend needs (measured, 2026-11-09, local PostgreSQL 17.5)
| Need | Measurement |
|---|---|
| Host process, idle | about 90 MB RSS (host alone, between safety-net cycles) |
| Host plus workers under load | about 570 to 590 MB peak process-tree memory (earlier load test); a 512 MB or 256 MB host will not hold it with the general lane at the default 2 |
| Database connections | the host and its workers peaked at 4 simultaneous connections while draining 600 events. The API adds up to 15 per API process at SQLAlchemy defaults (pool 5 + overflow 10) |
| Idle CPU | about 4.5 CPU-seconds per minute after probe gating |
| Storage | about 0.5x feed size per job-board refresh with `omit_fields`; DB size is the binding limit |

## Database candidates
| Provider | Evidence | Fit |
|---|---|---|
| Aiven free PostgreSQL | **Vendor pages read directly:** no card to sign up; 1 GB disk, 1 CPU, 1 GB RAM; `max_connections` 20, no connection pooling; may power off a service with no ongoing activity (notice sent, can be powered on again; idle length not stated); backups listed but retention not stated; no SLA; terms can change | Best verified fit. Our host keeps the service active; the 20-connection cap needs a smaller API pool (see below) |
| Neon free | Secondary sources only (not verified): 500 MB, no card, 100 compute-hours per month, compute suspends after the monthly limit | Poor fit for a worker that polls constantly: an always-awake database exhausts the monthly compute hours |
| Supabase free | Secondary sources only: 500 MB, pauses after a week of inactivity, no backups | Weaker; storage half of Aiven |
| Render / Railway free Postgres | Secondary sources: 30-day expiry (Render), trial credit (Railway) | Excluded: not permanently free |

Not verified: Aiven's backup retention and exact idle power-off rule, and whether 1 GB holds the planned job volume
(DEP-05 should add a quota monitor and an alert before the disk fills).

## DEP-01 findings (2026-11-09) - Neon Free, read from Neon's own pages
Sources: neon.com/faqs/free-plan-limits-and-quotas, neon.com/pricing, neon.com/docs/connect/connection-pooling,
neon.com/docs/guides/scale-to-zero-guide. These supersede the "secondary sources" Neon row above (which said 500 MB).

| Fact | Neon Free (official page) |
|---|---|
| Card | Not required; plan is permanent, not a trial |
| Storage | 1 GB per project, 20 GB across projects; writes that grow storage fail above the cap, no data deleted |
| Compute | 100 CU-hours per project per month; at the limit compute stays suspended until the next period (connections drop, no overage billing) |
| Scale to zero | Suspends after 5 minutes idle; cannot be disabled on Free; resume latency is "usually a few hundred milliseconds" (pricing page) |
| Restore | 6 hours of change history (capped at 1 GB) plus 1 manual snapshot: NOT enough alone; independent `pg_dump` backups are required |
| Pooling | Pooler endpoint (`-pooler` host), PgBouncer transaction mode, `max_client_conn` 10,000; pool size is 90% of `max_connections` (104 at 0.25 CU, so about 94); pooled and direct connections share `max_connections` |
| Pooler limits | No session `SET`, `LISTEN/NOTIFY`, session-level advisory locks, SQL-level `PREPARE`; use a direct connection for migrations and `pg_dump` |
| Idle inactivity | Free projects are not deleted or paused for inactivity |

Not verified: the Free plan's minimum compute size (so CU-hours per active hour), and the real resume time from this app.

**Constraint found in this repo (static reading of `app/workers/worker_host.py`, not yet measured at runtime):** the
always-on host evaluates a database work probe for every worker every `--poll-seconds` (5 s), and starts each worker at
least every `--safety-net-seconds` (120 s). That keeps the database active far more often than Neon's 5-minute idle
rule, so scale-to-zero would never engage. If the compute floor is 0.25 CU, always-on use is about 182 CU-hours per
month against a 100 CU-hour quota, so the database would be suspended from roughly day 17 (arithmetic only; the floor is
unverified). Therefore the always-polling host must NOT run against Neon Free. Use bounded scheduled cycles
(`worker_host --once`, or the per-worker `--once` runners from PR #98). Spacing only just over 5 minutes is NOT enough:
each database touch keeps compute awake for about 5 minutes after it. With a 0.25 CU floor (unverified) the monthly
budget of about 400 active hours is a 55% duty cycle, so the break-even touch interval is about 9 minutes; 15 minutes
is about 61 CU-hours and 30 minutes about 30 CU-hours, before work time and API traffic. This is arithmetic, not a
measurement. A low-duty idle mode for the host is proposed, not implemented, and needs owner authorization.
This is only viable once a scheduler/host that fits the five criteria exists.

**Backend compatibility with Neon's pooler:** the only session-sensitive database features found are
`pg_advisory_xact_lock` calls (transaction-scoped, so compatible) in `canonicalization.py`, `manual_ingestion.py`,
`source_limits.py` and `source_refresh_worker.py`; no `LISTEN/NOTIFY`, `SET`, temp tables or SQL `PREPARE` were found.
psycopg 3 uses protocol-level prepared statements, which Neon documents as supported (PgBouncer 1.22+). NOT VERIFIED at
runtime; run the integration suite through the pooler endpoint before relying on it.

**Pool sizing (not implemented):** `app/infrastructure/db/session.py` calls `create_engine(url, pool_pre_ping=True)`, so
each API process can open up to 15 connections (pool 5 + overflow 10). That fits Neon's roughly 94-connection pool
and exceeds Aiven's 20-connection cap once workers are added. Making `pool_size`, `max_overflow` and `pool_recycle`
settings is still a proposed change, scoped by the database chosen.

Measurement of Render cold start, Neon resume and quota use on a real free pair: see `free-tier-wake-measurement.md`
(runbook and `scripts/measure_free_tier_wake.py`). Results of the 2026-11-10 run are below; the run is PARTIAL.

## Measured on a real free Render + Neon pair (2026-11-10, PARTIAL)
Setup: Render Free web service `jobpunter-api` (Frankfurt, branch commit `da3822f`), Neon Free project `jobpunter-dev`
(Frankfurt, Postgres 17, 0.25 CU fixed). Migrations ran to `0082_resilient_discovery` at first start; `/api/v1/readiness`
returned `ready` with `database`, `schema_current` and `deployment_revision_configured` all true (owner screenshot).

| Measurement | Result | Basis |
|---|---|---|
| Render spin-down | Service shut down exactly 15 min 0 s after its last request, twice (04:43:10 to 04:58:10 UTC, 06:21:43 to 06:36:43 UTC) | Render logs |
| Render + Neon cold start | 87.3 s from a phone browser on 4G in Lagos (includes the owner's network latency) | One observation, owner stopwatch |
| Neon resume | About 0.5 to 0.7 s (`/readiness` 0.6 s and 0.9 s against 0.1 s and 0.2 s for `/health` on a warm Render) | Two probes; Neon's suspended state at those moments was inferred, not confirmed |
| Neon compute floor | 0.25 CU (active seconds times 0.25 matches compute seconds: 1620 s and 413 CU-s) | Neon `describe_project` counters |
| Neon usage during the run | 156 CU-s / 612 active s before; 413 CU-s / 1620 active s after (about 0.11 CU-hours of 100); last compute activity 06:21:44 UTC | Neon counters; includes startup migrations and the owner's visit |

**Failed finding: a Cloudflare Worker could not wake the sleeping Render service.** After Render spun down at 06:36:43,
every probe sent from a Cloudflare Worker cron (10 probes over about 3 hours at 10, 16 and 30 minute gaps, then 25
retries over 151.7 s using a browser-style User-Agent and Accept header) returned HTTP 502 within a fraction of a second.
The 502 carried the header `x-render-routing: no-deploy`. Render logged no instance start and no request at all from
06:36:50 to 11:15 UTC, no failure or suspension events existed, and the deploy was listed `live`. The same service had
been woken by the owner's phone browser at 05:56 UTC. The cause is NOT determined (3 of 5 diagnostic attempts used).
**Control test (owner, 11:42 Lagos / 10:42 UTC):** the same sleeping service woke for the owner's phone browser and
served `/api/v1/health` after 1 min 56 s (116 s). So the service is healthy and can wake; the failure is specific to the
Cloudflare Worker caller (the Worker's warm requests earlier did succeed, 200 in 0.1 to 0.9 s). The Render forum shows
another user with the same `no-deploy` 502 on a "live" service that a redeploy fixed temporarily, which this control
does not match (a redeploy was not needed here).
**Cold-start breakdown from Render logs (UTC):** request about 10:40:10; instance start command 10:40:51 (about 41 s of
platform wake); Alembic reaches the database 10:41:09 (+18 s); app imports finished 10:41:51 (+42 s); server ready
10:42:00 (+9 s); response 10:42:07. About 69 s of the 116 s is this application's own start-up (`alembic upgrade head`
plus importing the full dependency set), not Render's wake. Earlier cold start (05:56 UTC) was 87 s, so it varies.
**Status: B item, OPEN.** Required: a caller that can reliably wake the service (or a verified keep-awake design), and a
shorter start-up. Candidate routes, none yet tested: other external callers; a Worker keep-alive every 10 to 14 minutes
(warm requests worked, but uses about 730 of 750 instance hours); removing `alembic upgrade head` from the start command
(a Render setting) and a separate migration step.
Consequence: the "Cloudflare Cron calls a Render endpoint to wake it" design is NOT VERIFIED and failed in this test;
do not rely on it. It is not shown that other callers (for example GitHub Actions) behave the same way.

Not measured: cold start after 10, 16 and 30 minute gaps from a controlled caller, Render instance hours used (not exposed
to the tools used), and the longest background cycle that survives spin-down.

## Always-on host candidates

## Always-on host candidates
No always-on, card-free host with at least 600 MB RAM was verified. Findings:
- Free tiers that sleep on idle (Render web services, Koyeb) conflict with an always-polling host (secondary sources).
- Fly.io: secondary source says signup without a card and 256 MB VMs; the 256 MB size is too small for the full host.
  Its pricing page could not be read (blocked), so card and free-allowance terms are NOT VERIFIED.
- Oracle Cloud "Always Free" is generous but requires a card at signup: excluded by the rule.
- Your own computer or a spare device: zero cost, enough RAM, but uptime depends on power and the connection.
  Public reachability for the API would need a free tunnel (Cloudflare Tunnel is free per its public docs; not verified here).
- Scheduled one-shot runs (`python -m app.workers.worker_host --once` on a free scheduler such as GitHub Actions): the
  host already supports `--once` and PostgreSQL holds all state, so no always-on process is required. Limits and terms
  for private repositories (monthly minutes, minimum schedule interval, acceptable-use rules) are NOT VERIFIED and must be
  read before relying on this.

## Cloudflare R2 (documents)
Cloudflare's product page says "Start building for free, no credit card required" with 10 GB-month storage,
1 million Class A and 10 million Class B operations free. Several third-party guides say a payment method is needed to
enable R2. **Owner-confirmed 2026-11-10: the Cloudflare dashboard requires a card on file to enable R2.** This conflicts
with the "no card" rule above, so R2 is not zero-card. A card does not by itself mean a charge (free allowances apply),
but overage billing becomes possible; the owner will decide whether to add a card or choose a different document store.
Until then document storage is UNRESOLVED and nothing may depend on R2 being enabled. (On 2026-11-10 the connected
Cloudflare account listed no R2 buckets: the API returned 403, consistent with R2 not yet being enabled.)

## Resolved so far (2026-11-10)
- Neon project `jobpunter-dev` created: AWS Frankfurt (`aws-eu-central-1`), Postgres 17, 0.25 CU fixed compute, 1 GB,
  6 h restore. Postgres 17 matches the version the backend was verified on (17.5). Nothing is deployed to it yet.
- Render workspace "My Workspace" exists and is empty (no services, no databases). Free-tier availability in Frankfurt is
  NOT VERIFIED (Render's regions page lists Frankfurt but does not state free-instance availability per region).

## Proposed small changes (not implemented; need authorization)
1. Settings for the SQLAlchemy pool (`pool_size`, `max_overflow`) so the API stays under 20 connections on Aiven's free plan.
2. A database size and connection-count check in the admin operations summary (DEP-05 quota monitoring).

## Decisions needed from the owner
1. Is a computer the owner controls acceptable as the always-on host for the first release, or must hosting be cloud-only?
2. Try the Aiven free sign-up (no card) and confirm it. Send back the plan page and region.
3. Confirm Cloudflare R2 enables without a card.

## Render replacement search: Northflank Sandbox (2026-11-11, read from Northflank's own pages)
Owner decision (2026-11-10): a card on file is acceptable for identity verification; billing must stay at $0. A hard stop or hard cap is preferred.

| Question | Finding |
|---|---|
| Card | Billing docs: "all users must add a payment method to start creating resources ... regardless of plan selection". Pricing page says "No card needed". The two conflict; the docs are the stricter reading. |
| Plan limits | Sandbox: 2 services, 2 jobs, 1 addon. "Up to $20/mo of resources". Always on, no sleeping. |
| Prices | $0.01667 per vCPU-hour, $0.00833 per GB-hour, $0.11657 per GiB-month persistent disk. `nf-compute-50` (0.5 vCPU, 1 GB) is $12/month on the page. `nf-compute-20` is about $5.40/month (derived, not listed). |
| Hard spend cap | NONE found. Billing thresholds trigger invoicing; they do not cap spend. Billing alerts only notify. Services are suspended only when invoices cannot be paid. |
| Behaviour above the $20 allowance | NOT STATED in the pages read. |
| Production use | Docs: the free tier "should not be used for production applications". |
| Memory fit | Measured host plus workers under load is about 570 to 590 MB, so only the 1 GB plan fits (about $12 of the $20). |

Status: Northflank Sandbox FAILS the "hard stop" preference (no cap, overage behaviour unstated, card with a charge-at-cycle-end model) and is documented as non-production. It is a possible stop-gap only if the owner accepts uncapped billing exposure with billing alerts. NOT RUNTIME-VERIFIED.

## Render replacement search: other candidates (2026-11-11, official pages unless marked)
Test applied: card allowed for verification, billing must stay at $0, hard stop preferred, always on, production-suitable.

| Candidate | Card | Hard stop | Always on | Fit | Verdict |
|---|---|---|---|---|---|
| Oracle Always Free (Ampere A1: 1,500 OCPU-hours + 9,000 GB-hours a month = 2 OCPU / 12 GB; 2 AMD micro VMs at 1 GB; 200 GB block storage; 10 TB outbound) | Required for identity verification | Charges only apply to usage above Always Free AFTER upgrading to a paid account; Oracle's notice says excess Always Free compute is terminated, not billed (community report, unverified) | Yes (VM) | 12 GB is far above the 570 to 590 MB host peak | STRONGEST technical fit. Risks: idle reclaim if 95th-percentile CPU, network and memory stay under 20% for 7 days (the A1 shape is the one that includes memory); "out of host capacity" errors at creation; limits were cut in 2026 (A1 4 OCPU / 24 GB to 2 / 12, enforced Aug 18, community source). Must never upgrade the account. |
| Railway Free | Not stated | Not stated | Not stated | $1 a month credit, no rollover | Reject: covers "a cron job and little else". |
| Google Cloud (Cloud Run / e2-micro) | Required | Budgets only email. Spend caps are Preview, not hard caps, and cover Cloud Run only through Firebase products. | Cloud Run scales to zero | n/a | Reject on the hard-stop rule. |
| Azure App Service F1 | Required | Unstated | 60 CPU minutes a day | n/a | Reject: docs say free plan is not supported for production. |
| Northflank Sandbox | Required by billing docs | None | Yes | 1 GB plan only | Reject (see section above). |
| Koyeb free | Required ($29 hold) | Unverified | Unverified | 512 MB / 0.1 vCPU | Reject (memory too small). |
| Render Free (current) | Not required | Yes: at 750 instance hours free services are suspended, no overage | No: spins down after 15 idle minutes | Wake needs inbound traffic; keep-alive under test | Remains the working host; keep-alive result pending. |

Recommendation (PROVISIONAL, not runtime-verified): keep Render Free plus a keep-alive as the default while the test runs. Oracle Always Free is the best replacement candidate if the keep-alive fails or 750 instance hours are not enough. It needs an owner decision on the Oracle account (card for verification; do not upgrade to paid), then a capacity test, since A1 creation can fail with "out of host capacity". Oracle's idle-reclaim rule means a near-idle host could be reclaimed, so the plan must include steady load or the AMD micro shape.
NOT VERIFIED: Oracle's current signup requirements, A1 availability in the owner's region, the 2026 cut from Oracle's own notice.
