# JobPunter Admin Backend Gap Matrix

**Scope:** Initial repository-to-requirements reconciliation for the Admin backend workstream.  
**Governing sources:** JobPunter PRD + Technical Spec v1.5; JobPunter Admin Panel Requirements Specification v1.0, including its discovery/source/search appendix; JobPunter Admin Backend Implementation Task List; Autonomous Development Protocol.  
**Branch:** `admin-backend`  
**Pre-work baseline:** `06b30bf9356768d8382a4794d98fd4c976178ee0`  
**Current implementation checkpoint:** `ba4a55169ac23570311d10515ac2e6c450ee8275`  
**Verification:** CircleCI `backend-verification` passed for the checkpoint above. Local tests were not run in this environment. CI pass means the configured compilation, migration/schema checks and test suite passed; it does not prove live provider behavior or production runtime readiness.

## Status definitions

- **Verified (repository):** The relevant implementation path and regression coverage were inspected, and the exact checkpoint passed CI.
- **Partial:** A usable foundation exists, but one or more material requirements or permission/lifecycle paths remain open.
- **Missing in inspected surface:** No implementation was found in the inspected routes/models. This is not a claim that no related code exists anywhere in the repository.
- **Not verified:** Code/configuration exists, but external provider, deployment, or runtime evidence is absent.
- **Blocked / policy open:** Implementation must not infer a product/security policy that the governing sources leave unresolved.

A route, model, UI control, or green CI run is not sufficient by itself to mark a complete product workflow verified.

## Initial traceability matrix

| Requirement area | Evidence inspected | Status at checkpoint | Remaining work / acceptance evidence |
|---|---|---|---|
| Trusted identity and Admin boundary | `app/api/deps.py`; `app/api/v1/admin.py::_require_admin`; `AdminGrant`, `User`; `tests/unit/test_admin_route_authorization.py`; PostgreSQL governance tests | **Partial** | Shared active-ADMIN-grant enforcement exists. Fine-grained permission catalogue, read-only support roles, session expiry/revocation and step-up policy are not implemented/approved. Continue route-by-route and resource-level authorization tests. |
| Administrator membership | `AdminGrant`; `/admin/grants`, grant create/reactivate and revoke routes in `admin.py` | **Partial** | Current supported role is `ADMIN`; do not infer proposed role categories. Resolve role/permission matrix, onboarding method, self-revocation and last-admin rules before implementing policy-dependent behavior. Add membership lifecycle/concurrency tests. |
| User directory | `GET /admin/users`; PostgreSQL tests in `test_admin_governance_postgres.py` | **Partial** | Bounded pagination, name/email/exact-ID search, account status, email verification, support-status, registration-date and last-login bounds, allowlisted sorting and stable tie-breaker are implemented. General activity history is not equivalent to last login; resource-permission scoping remains open. |
| User detail and resource minimization | `GET /admin/users/{user_id}`; `CandidateProfile`, `CandidatePreferences`, documents, discovery, evaluations, Punts, applications, support, notifications | **Partial** | Returns profile/preferences and resource counts/status summaries, not document bodies/storage keys or support/message bodies. Add resource-level permissions and detail acceptance for all required domains. Recent support list is a metadata summary, not full support-history access. |
| Account lifecycle | `PATCH /admin/users/{user_id}/status`; account status integration tests | **Partial / policy open** | Changes use expected revision and atomic audit. PRD names ACTIVE/SUSPENDED/DEACTIVATED but does not define the full transition graph. Do not infer reactivation/appeal/duration/notification policy. Verify downstream restrictions on queued work, discovery and notification processing. |
| Administrative audit | `AdminAuditEvent`; `GET /admin/audit`; write paths in `admin.py`; filter tests | **Partial** | Audit reads now support actor, action, resource type/ID, outcome, time range, bounded pagination and stable ordering. Complete schema/outcome conventions, denied-attempt coverage, write-path completeness, immutability/retention controls and cross-domain audit coverage remain open. |
| Versioned configuration | `GovernanceConfigVersion`; `/admin/configs`, publish and rollback routes; PostgreSQL lifecycle tests | **Partial** | AI task and decision-rule configuration foundations exist. Inventory and connect all approved domains, verify every consumer uses the canonical active version, and cover concurrent publication/rollback end to end. |
| AI execution and decision governance | `AI_TASK_REGISTRY`, `AIExecution`, `/admin/ai-executions`, decision-rule preview and config validation | **Partial** | Traceability and server-owned task-key validation exist. Complete the approved task/model/fallback assignment governance, rule validation, permission separation and effective-version evidence for all governed operations. |
| Source registry and acquisition | `Source`, connector configuration versions, source permission review, health check, acquisition-run list/detail/run APIs in `admin.py` | **Partial** | Existing Greenhouse/source foundations are reusable. URL/crawl-target registry, schedule and orchestration governance, budgets/tool restrictions, source-specific permission review evidence, and live-source/runtime verification remain open. Configured status must not be reported as observed health. |
| Job resolution / shared Job Pool | `JobResolution`, resolution candidate models, source acquisition, outbox and work-item routes | **Partial** | Protected resolution review and operational inspection exist. Complete admin-facing pool inspection, freshness/liveness states, quarantine/recovery paths, provenance and end-to-end deduplication/partial-failure acceptance. |
| Impersonation | Inspected router, exported model registry and Admin route inventory | **Missing in inspected surface** | No dedicated persisted impersonation session or read-only enforcement path was found in the inspected surface. Implement only with a durable session, target scope, expiry/revocation, explicit audit, and tests proving all mutation/external-effect paths remain blocked. |
| Administrative interventions | Inspected Admin routes and exported model registry | **Missing in inspected surface** | No dedicated intervention catalogue/record was found in the inspected surface. Keep unspecified interventions unavailable. Map each approved intervention to canonical services, explicit authorization, administrator attribution and atomic audit. Application submission must remain prohibited. |
| Support | `SupportTicket`, `SupportMessage`; candidate/admin routes in `app/api/v1/support.py` | **Partial** | Ticket inbox, bounded filtering, detail, messages, status and assignment exist. Verify status transition semantics, permission scoping, idempotency/delivery outcomes, assignment policy, and candidate-context minimization. |
| Notifications and email delivery | `Notification`, `NotificationEmailDelivery`, notification service/worker, candidate notification routes, `GET /admin/notifications/email-deliveries` | **Partial** | In-app persistence and email attempt states exist; Admin can inspect persisted delivery state without message bodies or addresses. Verify all required event triggers, outbox idempotency/retry behavior and delivery truthfulness. SMTP/provider runtime is not verified. |
| Integration configuration and health | Existing source health check and provider settings; no general integration-management route identified in inspected Admin routes | **Partial / not verified** | Inventory providers and secret references. Separate configured, enabled, credentials-present, last-tested, operational, degraded, failed and unknown/stale states. Add bounded non-destructive checks and persisted observation history without exposing secrets. |
| Operational work and recovery | `WorkItem`, `OutboxEvent`; `/admin/operations/*` routes in `admin.py` | **Partial** | Inspection, retry/replay/cancel and selected recovery paths exist. Complete lifecycle transition matrix, recovery/retry safety per handler, worker/scheduler heartbeat and backlog metrics, alerts, and crash/restart evidence. |
| Schema, migration and API regression | Alembic/schema-parity and PostgreSQL test workflow; `test_admin_governance_postgres.py`; `test_admin_route_authorization.py` | **Verified for current configured CI gates; Admin acceptance remains partial** | Exact checkpoint passed CircleCI. Local execution and live staging/provider checks were not performed. Add focused Admin E2E acceptance and verify new migrations only when schema changes are required. |
| Runtime/release readiness | Repository runbooks and PRD gap audit | **Not verified** | Firebase live sign-in, live source access, SMTP, object storage, deployed workers/scheduler, restart recovery and backup/restore require authorized staging evidence. Do not infer them from unit/integration tests. |

## Work added on this branch

1. Protected, bounded Admin user directory with safe account/profile summaries, exact user-ID search, approved filters and allowlisted sorting.
2. Protected user detail with profile/preferences, discovery/job-search/document/application/support/notification summaries. Sensitive detail reads write an audit event; full document objects, storage keys, support bodies and notification bodies are not returned.
3. Protected Admin audit query filters for actor, action, resource, outcome and time range.
4. Protected email-delivery operations listing exposing persisted status/attempt/error metadata while omitting email addresses and notification bodies.
5. PostgreSQL integration tests for the new directory, detail, audit filtering and email-delivery routes.

## Next implementation order

1. Finish G1 acceptance: add activity-history evidence where canonical records support it; close remaining directory/detail filter and resource-scope gaps without equating last login with all activity.
2. Complete C1 audit coverage and authorization-denial logging using existing audit persistence.
3. Build the approved team permission model only after documenting the unresolved policy decisions; keep the current ADMIN grant as the only assumed role.
4. Implement durable, enforced read-only impersonation and separately authorized interventions, including migrations and adversarial tests.
5. Complete discovery control-plane gaps (URL registry, schedules/orchestration/budgets/tool policy) by extending existing source, work-item and outbox services.
6. Complete integration/notification operations and operational health, then run Admin cross-domain PostgreSQL acceptance and the final security/privacy review.

Frontend integration, merge and deployment remain separate gates.
