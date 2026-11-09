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
enable R2; that conflict is NOT VERIFIED. The owner should confirm by opening the dashboard R2 sign-up without a card.

## Proposed small changes (not implemented; need authorization)
1. Settings for the SQLAlchemy pool (`pool_size`, `max_overflow`) so the API stays under 20 connections on Aiven's free plan.
2. A database size and connection-count check in the admin operations summary (DEP-05 quota monitoring).

## Decisions needed from the owner
1. Is a computer the owner controls acceptable as the always-on host for the first release, or must hosting be cloud-only?
2. Try the Aiven free sign-up (no card) and confirm it. Send back the plan page and region.
3. Confirm Cloudflare R2 enables without a card.
