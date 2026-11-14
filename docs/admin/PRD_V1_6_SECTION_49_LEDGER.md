# PRD v1.6 §49 clause ledger (Admin & System Control Plane, User Records, Support)

**Historical checkpoint date:** 2026-11-04 · **Branch:** `main` · **Evidence head:** `4833b5dd117137f5c06d461c42ee3c21d119daf2` · **Source:** PRD + Technical Spec v1.6 §49.1–49.8 and the "User Records" and "Impersonation requirements" clauses.
Statuses use Protocol v3 vocabulary. "TESTED" means the named repository tests are covered by exact-head GitHub Actions run `41851083681`; CircleCI is not accepted as evidence. Nothing here is RUNTIME-VERIFIED because no staging/production target is available. Gap class: A = genuine later task, B = current-task omission, C = authoritative requirement missing from the task plan.

| Clause | Requirement | Status | Evidence (code → test) | Gap |
|---|---|---|---|---|
| 49.1 | Server-enforced admin authorization, explicit role state, audit | TESTED | `_require_admin`, `AdminGrant` (role `ADMIN` only) → `test_admin_route_authorization.py`, `test_admin_mutation_audit_matrix_postgres.py` | — |
| 49.1 | Protected `/admin` boundary and dedicated shell | PARTIAL | Backend boundary is `/api/v1/admin/**`; the admin frontend shell is outside this repository | A (frontend repo) |
| 49.1 | Least-privilege access | PARTIAL | Owner-approved ADMIN/SUPPORT_AGENT/OPERATIONS policy with explicit per-route checks, minimized support summary and allow/deny audit matrix (D2) | Final-head CI and deployed role acceptance remain open |
| 49.2 | Manage AI tasks/versions, providers/models, task-model assignments | TESTED (decision D5) | Code-owned `AI_TASK_REGISTRY`; admin publishes `ai.task.<id>` configs (instruction, provider, model, temperature, fallback) via `/configs`, validated in `admin.py`, consumed by `app/application/ai/service.py::_published_task_config` → `test_admin_governance_postgres.py`, `test_ai_task_execution.py` | CLOSED (was C) |
| 49.2 | Matching thresholds / decision rules | TESTED | `GovernanceConfigVersion`, `/configs`, `/decision-rules/preview` | — |
| 49.2 | Source registry, connector configuration, acquisition controls | TESTED | `/sources**`, `/discovery-targets**`, `/target-policy/**`, `/sources/{id}/acquisition-runs` | — |
| 49.2 | Schedules | TESTED (repository) | Candidate missions, persisted Admin discovery schedules, and per-source refresh schedules execute through the durable scheduler; migrations 0074–0081 and PostgreSQL schedule/worker tests | Runtime deployment NOT VERIFIED |
| 49.2 | Integration configuration and observed health | TESTED (repository) | `/integrations/status`, `/integrations/{key}/health-check`, `/integrations/observations`; live provider unverified | A (runtime gate) |
| 49.2 | Notifications settings | TESTED | `/smtp/configurations**` (test-before-publish, rollback), `/notifications/email-deliveries**` | live SMTP NOT VERIFIED |
| 49.3 | Draft/validated/published/superseded; immutable published; hash, author, previous version, audit ref; invalid cannot activate | TESTED | `GovernanceConfigVersion`, unique partial index one PUBLISHED per key → `test_admin_governance_postgres.py` | — |
| 49.4 | Rollback as new audited action | TESTED | `/configs/{id}/rollback`, `/smtp/.../rollback` | — |
| 49.5 | Runtime consumes authoritative published config; executions record version | PARTIAL | SMTP delivery records effective config version; connector and governance versions recorded by consumers; AI task contracts are code-owned (D5), while published `ai.task.<id>` assignments are consumed by `app/application/ai/service.py::_published_task_config` and execution records name the config version | Live provider acceptance NOT VERIFIED; M04 rechecks consumer evidence |
| 49.6 | Administrative audit: actor, time, action, target, outcome | TESTED | `AdminAuditEvent` append-only; failure handlers in `app/api/errors.py`; 37-route matrix + static guard | Runtime audit verification NOT VERIFIED |
| User Records | Directory, search, filters, sort, profile, status management | TESTED | `GET /admin/users`, `GET /admin/users/{id}`, `PATCH /admin/users/{id}/status` → `test_admin_governance_postgres.py` | — |
| User Records | Job-search, document, support overviews; activity history | TESTED | `GET /admin/users/{id}` summaries (document filenames, keys, checksums excluded) | — |
| User Records | Record navigation to support and audit | PARTIAL | IDs and counts returned; deep-link contract is a frontend concern | A |
| Impersonation | Reason, start/end, banner context, read-only, expiry, audit | TESTED | `/impersonation-sessions**`, `READ_ONLY_IMPERSONATION_API.md` | — |
| Impersonation | Separately authorized troubleshooting interventions with before/after | TESTED (decision D6) | Catalogue of five interventions, all admin-attributed and audited → `test_admin_action_inventory.py`, `ADMIN_ACTION_INVENTORY.md` | CLOSED (was C) |
| 49.8 | Candidate ticket creation, two-way messages, history | TESTED | `app/api/v1/support.py`, `test_support_ticket_notifications_postgres.py` | — |
| 49.8 | Message states sending/sent/failed and retry | TESTED (decision D4) | Idempotent resubmission returns the original; key reuse with another body is rejected → support integration tests | CLOSED (was B) |
| 49.8 | Unread indicators; search/filters; assignment; admin replies | TESTED | `candidate_unread`/`admin_unread`, `unread_only`, `/assignment` | — |
| 49.8 | Resolved/closed; follow-up ticket linked to prior | TESTED | `follow_up_to_ticket_id`; lifecycle OPEN→RESOLVED→CLOSED (D1); concurrency tests | — |
| 49.8 | Notifications: new/resolved/closed email + in-app to admin and user | TESTED (repository) | `app/application/support/notifications.py`; message/assignment events added | live SMTP NOT VERIFIED |
| 49.8 | Impersonation handoff from ticket | PARTIAL | Session API exists; ticket → record link carries `candidate_user_id` in ticket detail | A (frontend handoff) |
| Doc storage | 4 MB limit | TESTED | `MAX_DOCUMENT_BYTES = 4 MiB` in `app/application/documents/imports.py` | — |
| Doc storage | R2 source of truth, 30-day configurable retention, expiry respecting dependencies, checksum, time-limited links | PARTIAL | `app/application/documents/retention.py`, `document_retention_worker.py`: 30-day configurable, dependency-aware, per-document locking; expiry and failures now audited (`DOCUMENT_RETENTION_EXPIRED/FAILED`) → `test_document_retention_postgres.py`. Time-limited links (`storage_download_url_ttl_seconds`), checksum and 4 MiB limit exist. Database-metadata storage usage and configurable warning/critical thresholds are implemented (`test_admin_storage_usage_postgres.py`, `test_storage_usage_alerts.py`); provider-billed usage is NOT MEASURED; R2 live behavior NOT VERIFIED | Provider billing measurement and runtime gate |

## Queue after this pass

Closed 2026-10-31: AI administration (D5), interventions (D6), message retry (D4), retention inspection and auditing. Storage usage/threshold alerts are implemented in the repository; provider-billed usage is NOT MEASURED. Every runtime gate remains NOT VERIFIED. Current mop-up execution and mandatory next work are recorded in `A0_CLOSURE_STATUS.md`.
