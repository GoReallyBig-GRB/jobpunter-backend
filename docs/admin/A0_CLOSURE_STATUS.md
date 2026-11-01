# Admin A0 closure status and evidence

**Status date:** 2026-10-29  
**Branch:** `admin-work`  
**Evidence head before this ledger update:** `0b559cf9fbc889ab6d2d3d269e1c2512e49de4be`

This ledger distinguishes repository implementation and CI evidence from runtime/release evidence. No merge, deployment, staging claim, or live SMTP delivery is implied by a CI pass.

## Closure matrix

| Item | Current disposition | Evidence and remaining acceptance |
|---|---|---|
| A0-CLOSE-01 Discovery truthfulness | Implemented in code; CI passing at the evidence head | Discovery target resolution and distinct blocked/unavailable outcomes are covered by `app/workers/discovery_mission_worker.py` and `tests/integration/test_discovery_missions_postgres.py`. Deployed egress enforcement remains a separate A0-CLOSE-07 runtime gate. |
| A0-CLOSE-02 Support lifecycle integrity | Implemented in code; CI passing at the evidence head | Ticket locking, durable lifecycle state, and notification retry/idempotency coverage are in `app/application/support/service.py` and `tests/integration/test_support_ticket_notifications_postgres.py`. Deployed concurrency/runtime evidence remains separate. |
| A0-CLOSE-03 Identity portability | Provider-neutral API boundary implemented; Firebase remains the startup adapter | `app/ports/identity` defines the contract; `app/api/deps.py` resolves the configured provider from a registry and fails closed for unknown names. `Identity` maps provider subjects to durable internal PostgreSQL user IDs. Tests: `tests/unit/test_identity_provider_contract.py`. A second production provider is not introduced because the PRD's provider choice remains unresolved and the explicit startup decision is Firebase. |
| A0-CLOSE-04 Audit completeness | Central failure-outcome handling implemented; per-route integration acceptance remains partial | A route-by-route scan of `app/api/v1/admin.py` found success auditing for every material mutating endpoint; Admin grants and job identity resolution audit through their helpers/services. `app/api/v1/admin.py` captures request-scoped actor/session context. `app/api/errors.py` now rolls back before recording safe Admin HTTP rejection, request-validation, domain-error, and unexpected-failure outcomes, and fails closed if the failure audit cannot be persisted. Identity-resolution service failures now use this centralized handling rather than duplicate service-level audit records. Submitted values, credential material, and exception messages are not written to failure audit details. Focused tests cover validation failures, rejected grant mutations, rejected identity-resolution decisions, audit persistence failure, and transaction rollback in `tests/integration/test_admin_governance_postgres.py` and `tests/integration/test_job_resolution_admin_postgres.py`. CI is passing at the implementation head. 2026-10-31: the three support-admin mutating routes were missing the audit-context dependency and now have it (`app/api/v1/support.py`). `tests/integration/test_admin_mutation_audit_matrix_postgres.py` drives every mutating Admin and support-admin route (37) as an admin and requires no 5xx, no echo of submitted values, and a request-correlated audit record with an allowed failure outcome; `tests/unit/test_admin_mutation_audit_coverage.py` statically fails if a mutating handler neither audits nor is on a reviewed delegated allowlist. Success-path audit remains covered by the per-feature tests. Remaining acceptance: verify audit behavior against the deployed database/runtime (NOT VERIFIED). `/decision-rules/preview` is read-only and intentionally has no mutation-success audit. |
| A0-CLOSE-05 Configuration authority | SMTP backend configuration workflow implemented; frontend/runtime acceptance remains | Admin API supports versioned SMTP drafts, safe listing, connectivity test, test-before-publish, publishing, and rollback-as-new-draft. Credentials are Fernet-encrypted at rest and never returned or placed in audit details. The notification worker consumes the published configuration, and the effective configuration version is recorded on each delivery (consumer traced 2026-10-31; connector and governance versions are likewise recorded by their consumers). `GovernanceConfigVersion` remains the existing versioned mechanism for non-secret governance payloads; SMTP credentials are deliberately kept out of generic JSON config. |
| A0-CLOSE-06 Requirements/invariant ledger | Reconciled in this document against the three sanctioned Project sources | Governing sources: Autonomous Development Protocol v3, JobPunter PRD + Technical Spec v1.6, and Admin Backend Implementation Task List (+others), including its canonical gaps register. No older Library copy or parallel gap register is authoritative. Each A0 item is mapped here to code/test evidence and remaining acceptance. |
| A0-CLOSE-07 Runtime/release gate | Deferred by user; release remains **NOT PASSED** | Requires an authorized target environment, deployed API/worker/scheduler evidence, live provider connectivity, observed egress enforcement, and a backup/restore exercise with integrity checks. CI is not evidence of these runtime conditions. |
| A0-CLOSE-08 Admin User Records | Core summaries and saved-job persistence implemented; final acceptance remains | `GET /api/v1/admin/users/{user_id}` exposes bounded document/package metadata, saved-job counts/recent entries, recent evaluations, application counts/statuses/recent entries, discovery mission progress, notifications, support summaries, and targeted audit activity. Private document filenames, object keys, checksums, raw content and private support/notification bodies remain excluded. A candidate saved-job model, migration, and owned/idempotent save/list/remove routes are present in `app/api/v1/saved_jobs.py`. Tests: `tests/integration/test_admin_governance_postgres.py`. |
| A0-CLOSE-09 Notification/SMTP governance | Core repository workflow implemented; live acceptance and runtime secret provisioning remain | Admin can configure any compatible SMTP provider by host, port, STARTTLS or implicit TLS, username/password, sender, event policy, and retry policy without a code change. Passwords are encrypted using `JOBPUNTER_SMTP_SECRET_ENCRYPTION_KEY`; provider configs are versioned and audited. The worker consumes the published config, applies event/retry policy, attempts the most recent retired config as last-known-good fallback, and records effective config version/fallback usage in delivery diagnostics. Tests include `tests/unit/test_smtp_config_security.py` and SMTP/Admin integration coverage. A stable encryption key must be provisioned through the runtime secret manager, and live provider acceptance remains unverified. |

## Environment and access notes

1. The three sanctioned Project documents are accessible in this session. Older Library copies and unsanctioned repository documents are not governing sources.
2. An earlier local clone attempt failed on GitHub DNS resolution. As of 2026-10-31 the suite has been run locally (PostgreSQL 16, non-UTC session, tests/live excluded): see the reconciliation section of `A0_V1_6_REQUIREMENT_CROSSWALK.md`. Remote CI is evidence only for the exact commit SHA it reports; local runs are supporting evidence, not CI evidence.
3. Staging/production access and real SMTP provider credentials have not been established. A0-CLOSE-07 remains deferred and release remains **NOT PASSED**.
4. Protocol v3 sets a 130-commit hard limit per working branch/PR, with a checkpoint at commit 120 and no commit 121 before merge. Separately, it requires a checkpoint after 3 consecutive batches or 15 tasks, whichever comes first. Fifteen tasks is not the commit cap.
5. Commit count: PR #91 reported 114 commits via the GitHub API at 61dd7f8 (the local clone is shallow; use the API). Earlier: `admin-work` was 107 commits ahead of `main` and 0 behind before the 2026-10-31 reconciliation commits. The 130-commit hard limit and the commit-120 assessment point apply; the crosswalk branch was folded in as a single squashed commit rather than by merging its 52-commit history. No merge is authorized by this ledger.
6. Secret encryption key requirement is documented in `.env.example`. The real key must be generated and stored in the runtime secret manager; it must never be committed.
7. A0-CLOSE-05 and A0-CLOSE-09 repository work must not be described as live-provider acceptance. SMTP test/publish endpoints require connectivity from the deployed backend to the configured provider.

## Remaining implementation and acceptance

- Route-by-route audit failure-outcome matrix: done 2026-10-31 (see A0-CLOSE-04). Runtime audit verification remains.
- Verify CI on the final candidate SHA after the current implementation batch.
- Provision `JOBPUNTER_SMTP_SECRET_ENCRYPTION_KEY` securely in the target runtime, configure an actual provider through the Admin API, test connectivity, publish it, and verify a real delivery plus fallback diagnostics.
- Integrate the SMTP API with the Admin frontend in its authorized repository/work batch; backend API completion alone does not establish that the control panel UI is connected.
- Keep A0-CLOSE-07 deferred and the release **NOT PASSED** until the required environment evidence is produced and reviewed.
- Policy decisions for support lifecycle/notifications, administrator roles and audit retention/export were made on 2026-10-31 and are recorded in `POLICY_DECISIONS.md`. Support message/assignment notification events and the two-session ticket-concurrency tests are implemented and tested; audit export and the role-enforcement matrix remain queued.

## Batch on `admin-clearance` (2026-10-31)

Closed with evidence: document retention inspection and expiry/failure auditing (PRD §50); support message retry semantics (D4); AI task administration (D5); intervention catalogue and whole-surface action inventory (D6, L3.2, H2.1); §49 clause ledger queue items. Decisions are in `POLICY_DECISIONS.md`.

Still open, by Task List section (all PARTIAL, none COMPLETE):
- **A0.2–A0.6, L3.3/L3.4:** requirement rows in `ADMIN_BACKEND_GAP_MATRIX.md` still aggregate several acceptance items; requirement-level split not done.
- **B (roles/session policy):** D2 single-role decision stands; session duration (900 s impersonation TTL) provisional.
- **E3–E5:** independent schedules, strategy/budget enforcement, durable per-step history, metrics/alerts (A, planned later).
- **F, G, K, L2/L3:** end-to-end acceptance matrices, exact-head runtime gates, backup/restore (NOT VERIFIED).
- **K2.4/E5.5:** storage usage monitoring and cost alerts (A).

## Batch 2 (admin-clearance) — L1.5 redirect matrix

- **L1.5 / E2.5-04 redirect cells: IMPLEMENTED + TESTED.** `tests/unit/test_redirect_security_matrix.py` runs 160 cases (JSON_API, RSS_ATOM, GREENHOUSE acquire, GREENHOUSE health_check × redirect statuses 301/302/303/307/308 × 8 hostile `Location` values: loopback v4/v6, RFC1918 10/8 and 192.168/16, link-local metadata 169.254.169.254, `file://`, protocol-relative, off-policy public host). Each case asserts exactly one request (the configured URL), no request to the `Location`, `follow_redirects=False` on every stream, no records, a non-success outcome, and category `REDIRECT_BLOCKED`.
- Local evidence: full suite against PostgreSQL 16 with `PGTZ=UTC`: 2214 passed, 1 skipped.
- **Still PARTIAL / NOT VERIFIED:** Scrapy and Playwright cross-entry redirect cases (browser route-abort is covered by existing unit tests but is not part of this matrix), JobSpy (fails closed by design), deployed network-egress enforcement, and live provider behavior.
- **A0.2–A0.6 requirement-level split: DONE** in `A0_REQUIREMENT_LEVEL_REGISTER.md` (22 acceptance items; none COMPLETE). Section F multi-route convergence (`test_multi_route_convergence_postgres.py`), Section G cross-user isolation (`test_admin_user_detail_isolation_postgres.py`) and Section K crash-restart recovery (`test_outbox_dispatcher_postgres.py`) added; they remain PARTIAL at section level.

## Batch 3 (admin-clearance)

- **Defect fixed (A0R-16 / G2.1):** the email worker opened SMTP for suspended/deactivated accounts. It now records `SKIPPED_INACTIVE_ACCOUNT` (no SMTP, not retryable). Regression: `test_email_delivery_never_opens_smtp_for_a_non_active_account`.
- **Defect fixed (F1.5):** a raw record with no usable title raised and stayed `ACQUIRED`, leaving only a generic outbox error. It is now rejected durably (`JobResolution` status `REJECTED`, method `VALIDATION_MISSING_TITLE`, raw `REJECTED`, discovery event `FAILED` with reason `MISSING_TITLE`), never retried, never fabricated into a job, and replay-stable.
- **Added evidence:** admin GET leak sweep (A0R-03), configuration-domain inventory guard (A0R-08, C2.1), Scrapy DNS-guard and Playwright destination matrices (L1.5).
- **Finding proposed, not changed:** all acquisition address guards use `ipaddress.is_global`, which accepts multicast ranges; not an SSRF path for TCP HTTP. A shared classifier would touch ~14 call sites and needs authorization (anti-drift rule).

## Batch 4 (admin-clearance)

- **Defect fixed (A0R-16 / G2.1):** scheduled discovery kept running for suspended/deactivated owners and queued missions kept executing. `schedule_due_missions` now skips non-active owners (resumes on reactivation) and the mission worker cancels queued missions with `ACCOUNT_NOT_ACTIVE`, creating no source runs and no completion notification.
- **Defect fixed (F1.6):** nothing ever set `freshness_status = STALE`, so a job whose verification kept failing stayed FRESH forever (PRD 15.5/45: seeing a job again does not prove it is open). Executive decision (PRD defines states but no window): `STALE_AFTER = 72 h` (three 24 h re-verification cycles). `mark_stale_jobs` runs at the start of each job-verification worker cycle; lifecycle is untouched, terminal jobs keep verified state, a successful ACTIVE verification restores FRESH.
- **Added evidence (A0R-03):** `test_admin_detail_routes_isolation_postgres.py` (14 cases).
- Local: full suite against PostgreSQL 16 with `PGTZ=UTC`: 2330 passed, 1 skipped. Remote CI on the exact head: pending until pushed.
- **Open B:** F1.7 send-time liveness. Runtime gates remain NOT VERIFIED.
