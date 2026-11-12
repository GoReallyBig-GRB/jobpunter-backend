# Agent Handoff: Next Execution Queue (supervised)

Written 2026-11-12 by the supervising agent. Audience: a second coding agent working while the
supervisor is unavailable. The supervisor reviews everything on return; nothing here counts as
COMPLETE until the supervisor accepts the evidence.

## 1. Authority and hard limits (read first)
Governing documents: Autonomous Development Protocol v3 and `docs/ENGINEERING_MISSION.md`
(project instructions). Where this file and the Protocol disagree, the Protocol wins.

- Repo: `GoReallyBig-GRB/jobpunter-backend`. Branch: `dep-04/agent-handoff-queue`, created from
  `main` at `86d9700` (the previous branch `dep-03/hosting-db-pool-monitoring` is fully merged into
  `main`; do not use it). Hard cap 130 commits on this branch, assess at 120. Work only here.
- **No new branches may be created after this one, by anyone, for any reason.** If the cap or scope
  forces a split, stop and ask the owner. Do not push to `main`.
- Do NOT: merge, open or ready a PR, create or switch branches (the owner has fixed the branch list), force-push, rewrite history,
  deploy, change Render/Neon/Cloudflare/cron-job.org settings, rotate credentials, or spend money.
- Never ask for, print, log or commit secrets (passwords, connection strings, R2 keys). The owner
  does not paste these in chat; they enter them in the provider dashboards.
- Smallest coherent change. No refactors of working code, no speculative abstractions. Propose
  optimisations in plain English instead of making them.
- Retry ceiling: 5 targeted attempts per underlying failure, then stop, mark BLOCKED, revert that
  file to the last stable state, write down the roadblock, and wait.
- Do not weaken tests or acceptance criteria to get green.
- Every claim needs evidence (exact command, actual output). Unverified means "NOT VERIFIED".
- Owner is not a developer: any message meant for the owner must be plain language, say what
  changed and what to expect, and avoid multi-step dashboard chores.

## 2. Verified baseline
- Local full suite at `86d9700` (same code as `1f6b7ea` plus this file): 2784 passed, 12 skipped, 0 failed.
- Remote CI for this head: NOT VERIFIED (no runs found).
- Local environment: Postgres 16 data dir `/var/tmp/pgdata` (start with
  `pg_ctl -D /var/tmp/pgdata -l /var/tmp/pg.log -w start` if `pg_isready -h localhost` fails),
  `DATABASE_URL=postgresql+psycopg://jobpunter:jobpunter@localhost:5432/jobpunter`, `PGTZ=UTC`,
  venv `/tmp/claude-0/venv-jp` (recreate from the repo requirements if missing).
- Lint baseline: `app/api/v1/admin.py` has 257 pre-existing ruff findings. Do not add any.
  New/edited files must stay at 0 new findings.

## 3. Task queue (do in this order)

### Q1. R-02: production sitemap fetcher (Protocol class C, mandatory)
Source: `docs/admin/A0_CLOSURE_STATUS.md` row R-02. Code: `app/infrastructure/discovery/sitemap.py`
(`SitemapDiscoveryProvider` takes an injected `fetcher`; no production fetcher exists and the
provider is not instantiated under `app/`).

Step 0 (gate): find the PRD / Technical Spec clause that requires sitemap discovery in production.
The project docs are in the Claude project (`JobPunter PRD ... [v-1.6].docx`, `Technical
Implementation Specification Expanded_v1.6`); the repo copy of the spec is
`docs/JobPunter Technical Implementation Specification Expanded_v1.6.docx`.
- If the clause exists: implement below.
- If it does not: do not build the fetcher. Record that in R-02, make the provider explicitly
  disabled and fail-closed with a test, and report.
- If unclear: stop and report the question. Do not guess.

Implementation (only if required):
1. A `SitemapFetcher` on the existing shared bounded, pinned-address, no-redirect transport (the
   one the other connectors use; find it via `PublicHttpJsonLdConnector` and
   `tests/unit/test_redirect_security_matrix.py`). Enforce content-type and size limits and the
   same address validation. Do not write a new HTTP client.
2. Add a sitemap runner to the redirect security matrix `RUNNERS`, with redirect and hostile-address
   cases (private IPs, redirects, oversize, wrong content type).
3. Register it in the discovery wiring so it is reachable from a real caller.
4. A PostgreSQL discovery test in `tests/integration/test_resilient_discovery_postgres.py` style
   proving the path end to end.

Acceptance: matrix cases pass; the integration test passes; the full suite still passes; ruff shows
no new findings; R-02 row updated with evidence and status.

### Q2. Migrations in the Render start command (class B, evaluate first)
Question: does the live API service run `alembic upgrade head` before serving? Read the service's
start command (Render tools: `get_service` for `srv-dbpa0b142hec73c1ispg`) and the repo's start
scripts. Read-only. Report what runs today, what happens on a cold wake with an unmigrated DB, and
a recommendation in plain English. Make no change to Render or the repo without supervisor approval.
Output: a short evidence note appended to `docs/operations/zero-cost-hosting-decision.md`.

### Q3. R2 wiring (class B): code side only
Goal: document storage via Cloudflare R2 through the existing storage abstraction.
1. Inspect the current storage provider interface and settings; list the exact env var names the
   code reads for R2 (endpoint, bucket, access key id, secret, region/prefix).
2. If an R2 provider already exists, verify it with tests against a fake S3-compatible endpoint
   (no real keys). If not, implement the smallest provider behind the existing interface, with
   tests for put/get/delete, key-prefix safety, size limit, and error mapping.
3. Add the variable names (no values) to `.env.example`.
4. Produce a plain-language owner note listing exactly which values to paste into Render and what
   success looks like (for example a health endpoint field). The agent must NOT set the keys.
Live R2 behaviour stays NOT VERIFIED until the owner enters the keys and the supervisor checks.

### Q4. Reporting
After each task: run the relevant tests, then the full suite, then ruff on changed files. Commit
small, meaningful commits to the branch with a message ending in the attribution lines the
supervisor uses (see recent `git log`). Push only to `origin dep-04/agent-handoff-queue` after `git fetch origin main`.

## 4. Stop conditions (stop and report, do not improvise)
- Any security, data-integrity or authorisation doubt; any failing invariant you cannot fix in 5
  attempts; PRD ambiguity; needing a secret or a dashboard action; commit count near 120.

## 5. Required report (exact headings, per Protocol section 11)
Completed Tasks & Evidence / Outstanding/Partial Tasks (class every gap A, B or C) / Test & CI
Results / Requirements Coverage / Blockers & Risks / Next Batch Plan. Include session commit count
and branch total. Put the report in `docs/operations/handoff-reports/<date>-<n>.md` and commit it.

## 6. Supervisor review checklist (for the returning supervisor)
- [ ] `git log` shows only expected commits, no history rewrite, count under cap.
- [ ] Diff touches only files named by the tasks; no secrets; no unrelated refactors.
- [ ] Re-run full suite and ruff myself; compare with the claimed numbers.
- [ ] Q1: PRD clause cited (or explicit-disabled path tested); matrix cases are real, not weakened.
- [ ] Q2: findings match what the live service actually shows.
- [ ] Q3: no keys anywhere; owner note is plain and checkable.
- [ ] Remote CI status retrieved for the final head SHA.
- [ ] Status labels honest (COMPLETE only with all gates; otherwise PARTIAL/NOT VERIFIED).

## 7. Still open and not part of this handoff
Always-on worker host (class A, DEP-01), Neon password rotation before real data (class A),
Render instance-hours discrepancy recheck, Oracle fallback. Do not touch these.
