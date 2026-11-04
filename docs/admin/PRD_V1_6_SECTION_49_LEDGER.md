# PRD v1.6 §49 clause ledger (Admin & System Control Plane, User Records, Support)

**Date:** 2026-10-31 · **Branch:** `admin-work` · **Source:** PRD + Technical Spec v1.6 §49.1–49.8 and the "User Records" and "Impersonation requirements" clauses.
Statuses use Protocol v3 vocabulary. "TESTED" = a named test passes locally (PostgreSQL 16, UTC) and in CircleCI on the head SHA; nothing here is RUNTIME-VERIFIED (no staging/production exists). Gap class: A = genuine later task, B = current-task omission (next-execution queue), C = authoritative requirement missing from task plan (next-execution queue).

| Clause | Requirement | Status | Evidence (code → test) | Gap |
|---|---|---|---|---|
| 49.1 | Server-enforced admin authorization, explicit role state, audit | TESTED | `_require_admin`, `AdminGrant` (role `ADMIN` only) → `test_admin_route_authorization.py`, `test_admin_mutation_audit_matrix_postgres.py` | — |
| 49.1 | Protected `/admin` boundary and dedicated shell | PARTIAL | Backend boundary is `/api/v1/admin/**`; the admin frontend shell is outside this repository | A (frontend repo) |
| 49.1 | Least-privilege access | PARTIAL | Single `ADMIN` role; candidate private content excluded from User Records (decision D2) | A (role matrix, POLICY_DECISIONS D2) |
| 49.2 | Manage AI tasks/versions, providers/models, task-model assignments | NOT VERIFIED / likely UNDONE | `AI_TASK_REGISTRY` is code-defined (`app/application/ai/task_registry.py`); no admin route creates versions, providers or routing | **C** – candidate for next lineup (see below) |
| 49.2 | Matching thresholds / decision rules | TESTED | `GovernanceConfigVersion`, `/configs`, `/decision-rules/preview` | — |
| 49.2 | Source registry, connector configuration, acquisition controls | TESTED | `/sources**`, `/discovery-targets**`, `/target-policy/**`, `/sources/{id}/acquisition-runs` | — |
| 49.2 | Schedules | PARTIAL | Discovery missions exist; independent schedules are Task List E3 | A (E3) |
| 49.2 | Integration configuration and observed health | TESTED (repository) | `/integrations/status`, `/integrations/{key}/health-check`, `/integrations/observations`; live provider unverified | A (runtime gate) |
| 49.2 | Notifications settings | TESTED | `/smtp/configurations**` (test-before-publish, rollback), `/notifications/email-deliveries**` | live SMTP NOT VERIFIED |
| 49.3 | Draft/validated/published/superseded; immutable published; hash, author, previous version, audit ref; invalid cannot activate | TESTED | `GovernanceConfigVersion`, unique partial index one PUBLISHED per key → `test_admin_governance_postgres.py` | — |
| 49.4 | Rollback as new audited action | TESTED | `/configs/{id}/rollback`, `/smtp/.../rollback` | — |
| 49.5 | Runtime consumes authoritative published config; executions record version | PARTIAL | SMTP delivery records effective config version; connector and governance versions recorded by consumers; **AI execution records task version** (`ai-executions`, `latency_ms`) but tasks are code-defined, not admin-published | tied to 49.2 AI row (C) |
| 49.6 | Administrative audit: actor, time, action, target, outcome | TESTED | `AdminAuditEvent` append-only; failure handlers in `app/api/errors.py`; 37-route matrix + static guard | Runtime audit verification NOT VERIFIED |
| User Records | Directory, search, filters, sort, profile, status management | TESTED | `GET /admin/users`, `GET /admin/users/{id}`, `PATCH /admin/users/{id}/status` → `test_admin_governance_postgres.py` | — |
| User Records | Job-search, document, support overviews; activity history | TESTED | `GET /admin/users/{id}` summaries (document filenames, keys, checksums excluded) | — |
| User Records | Record navigation to support and audit | PARTIAL | IDs and counts returned; deep-link contract is a frontend concern | A |
| Impersonation | Reason, start/end, banner context, read-only, expiry, audit | TESTED | `/impersonation-sessions**`, `READ_ONLY_IMPERSONATION_API.md` | — |
| Impersonation | Separately authorized troubleshooting interventions with before/after | UNDONE | No intervention allowlist implemented; spec permits "only separately authorized" actions, product has not agreed any | **C** – needs a product decision on which interventions exist; queued, not invented |
| 49.8 | Candidate ticket creation, two-way messages, history | TESTED | `app/api/v1/support.py`, `test_support_ticket_notifications_postgres.py` | — |
| 49.8 | Message states sending/sent/failed and retry | PARTIAL | `delivery_status` and message idempotency keys exist; no explicit failed-state retry route verified | **B** – verify/implement |
| 49.8 | Unread indicators; search/filters; assignment; admin replies | TESTED | `candidate_unread`/`admin_unread`, `unread_only`, `/assignment` | — |
| 49.8 | Resolved/closed; follow-up ticket linked to prior | TESTED | `follow_up_to_ticket_id`; lifecycle OPEN→RESOLVED→CLOSED (D1); concurrency tests | — |
| 49.8 | Notifications: new/resolved/closed email + in-app to admin and user | TESTED (repository) | `app/application/support/notifications.py`; message/assignment events added | live SMTP NOT VERIFIED |
| 49.8 | Impersonation handoff from ticket | PARTIAL | Session API exists; ticket → record link carries `candidate_user_id` in ticket detail | A (frontend handoff) |
| Doc storage | 4 MB limit | TESTED | `MAX_DOCUMENT_BYTES = 4 MiB` in `app/application/documents/imports.py` | — |
| Doc storage | R2 source of truth, 30-day configurable retention, expiry respecting dependencies, checksum, time-limited links | NOT VERIFIED | Retention and storage-adapter code was not inspected in this pass | **B** – inspect and record |

## Queue produced by this ledger (mandatory next execution)

1. **C – AI task/version/provider/routing administration (49.2, 49.5):** confirm whether the code-defined registry satisfies "manage AI tasks and task versions" or whether admin-published task versions are required; if required, implement via `GovernanceConfigVersion`. First step is the Task List's AI-governance requirement (Part II Scope D), not code.
2. **C – Administrative troubleshooting interventions:** requires a product decision naming the allowed interventions; none will be invented.
3. **B – Support message failed-state retry (49.8).**
4. **B – Document retention/R2/expiry inspection (Document Storage and Retention).**
