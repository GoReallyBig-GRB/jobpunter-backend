# Admin policy decisions

**Decided:** 2026-10-31, by the product owner (Ijeoma), who delegated these three open decisions with the instruction to choose what is best for the product.
**Status:** DECIDED (policy). Implementation status is stated per decision and is **not** implied by this record.
**Revisable:** yes. A later decision supersedes this one only by editing this file in a reviewed change.
**Sources:** PRD + Technical Spec v1.6 (§49 Admin Control Plane), Admin Backend Implementation Task List (+others), Autonomous Development Protocol v3. These decisions fill gaps those sources leave open; they do not override them.

**Database session time zone:** production PostgreSQL sessions run in **UTC** (product owner confirmation, 2026-10-31). Code must still compare instants rather than serialized strings; the tests run under UTC and a non-UTC session.

Principles applied: least privilege; fail closed; keep immutable history; do not add capability that cannot be enforced and tested; do not add anticipatory code.

---

## D1. Support lifecycle and notifications

### Decision

1. **Lifecycle:** `OPEN → RESOLVED → CLOSED`.
   - `RESOLVED` means an administrator has delivered a resolution; the candidate is notified.
   - `CLOSED` is the final administrative step, allowed **only from `RESOLVED`**, and is terminal.
   - **No reopening.** The candidate continues through a linked follow-up ticket, which preserves an immutable record of each issue.
   - New messages are rejected on `RESOLVED` and `CLOSED` tickets; an idempotent retry of an already-persisted message still returns the original.
2. **Assignment is optional.** Unassigned tickets are visible to every active administrator. Only an active administrator on an `ACTIVE` account can be assigned.
3. **Notifications** (in-app is the source of truth; email follows the candidate's consent and the published SMTP configuration, and is never required):

| Event | Recipient | Status |
|---|---|---|
| Ticket created | The candidate (confirmation) and active administrators | Implemented |
| Ticket resolved / closed | The candidate and active administrators | Implemented |
| Administrator message | The candidate only | Implemented 2026-10-31 |
| Candidate message | The administrator assigned **when the event is handled**, if still an active admin; if unassigned, no notification (the `admin_unread` queue indicator is the signal) | Implemented 2026-10-31 |
| Assignment or reassignment | The newly assigned administrator only (never the candidate; self-assignment and unassignment send nothing) | Implemented 2026-10-31 |

Created/resolved/closed keep their earlier, tested recipient set (candidate plus active administrators); this record previously mis-stated them as narrower. Email policy keys are the delivered notification types: `SUPPORT_TICKET_CREATED`, `SUPPORT_TICKET_RESOLVED`, `SUPPORT_TICKET_CLOSED`, `SUPPORT_TICKET_MESSAGE_ADDED`, `SUPPORT_TICKET_ASSIGNED`. `SUPPORT_TICKET_STATUS_CHANGED` is accepted for backward compatibility but matches no delivered notification.

Idempotency keys derive from the message id, or from a per-assignment change key, so retries and partial fan-out never duplicate and an A→B→A reassignment notifies each time.

### Rationale
The no-reopen rule and the RESOLVED-before-CLOSED rule are already enforced by `app/application/support/service.py` and tested, so this ratifies existing behavior. Notifying on every candidate message to every administrator would create noise that hides real tickets, so fan-out is limited to a named recipient.

### Implementation status
Done (2026-10-31): the three events, idempotent handling, dispatcher registration, corrected email policy keys, and PostgreSQL tests including two real-session concurrency tests (concurrent resolve applies once with one event; close cannot precede resolution).

---

## D2. Administrator role catalogue

Superseded 14 November 2026 by the owner: add SUPPORT_AGENT and OPERATIONS alongside ADMIN. AUDITOR remains reserved and ungrantable. The prior single-role launch decision is historical.

| Role | Allowed scope | Excluded scope |
|---|---|---|
| ADMIN | Every classified Admin route; access grants, governance, secrets configuration and recovery | Candidate application submission through Admin/inspection remains unavailable |
| SUPPORT_AGENT | Ticket inbox/detail, replies, assignment and lifecycle; bounded user directory and identity/status-only support summary | Full user detail/profile/evidence, account status changes, inspection sessions, grants, source/operations controls, SMTP, audit export |
| OPERATIONS | Source health/acquisition/status controls, discovery targets/policies/schedules/budgets, operational history/metrics/alerts and bounded retries/cancellation, integration health, sanitized delivery inspection/retry | Candidate directory/profile/support content, inspection sessions, grants, generic/AI/SMTP/strategy configuration and audit export |

The exact method-and-route matrix is server-owned in `app/security/admin_permissions.py` and documented in `ADMIN_ROLE_PERMISSIONS.md`. Unclassified endpoints fail closed. Active grants combine permissions; inactive grants, inactive users, unsupported roles and client-supplied role claims grant nothing. Every route is covered by an allow/deny and denial-audit matrix. Support tickets retain the historical ADMIN sender/actor-side label; the immutable actor user ID identifies the actual staff member.

SUPER_ADMIN alone can create/reactivate/revoke grants (D8 supersedes the earlier ADMIN authority), and only existing active accounts can receive them. No invitation flow, permanent break-glass account or second-approver workflow is added. The owner cannot self-revoke. Before owner bootstrap, the final usable ADMIN account remains protected against suspension; after bootstrap the owner remains available even when other full-administrator grants are revoked. Access-loss operations lock grants and account rows in a consistent order. Staff grants can be revoked while a single full ADMIN remains. The audited bootstrap runbook remains the recovery procedure.

Support assignments and ticket notifications include active SUPPORT_AGENT grants, never OPERATIONS-only grants. Discovery alerts include active OPERATIONS grants; delivery rechecks access and verified email, and users holding both ADMIN and OPERATIONS receive one digest.

Implementation and tests are on the mop-up branch, not deployed. Required exact-head CI and runtime acceptance remain separate gates.

---

## D3. Audit retention and export

### Decision

1. **Retention:** Admin audit events are append-only (database-enforced) and are kept **indefinitely in the primary store** for the MVP. There is no automated deletion. Before any archival, events must be at least 24 months old, archival must **move** (never destroy) the events, and the archival action must itself be audited.
2. **Export:** Allowed to `ADMIN` only (and `AUDITOR` once that role exists). An export requires an explicit date range of at most 90 days, is streamed (JSON Lines or CSV), and is rate-limited.
3. **Exported fields:** event id, timestamp, actor user id, action, resource type and id, outcome, request id, and the already-sanitized `details`. No secrets, credentials, candidate document content, or message bodies.
4. **Every export is itself audited:** actor, range, row count and format only.
5. **Personal data:** audit events reference users by id and contain no private candidate content, which keeps them minimized. A privacy or legal requirement for deletion or a shorter retention period overrides this default and must be recorded here.

### Implementation status
Export: IMPLEMENTED + TESTED (2026-11-02) as `GET /api/v1/admin/audit/export` (`test_admin_audit_export_postgres.py`): `ADMIN` only; `created_after` and `created_before` required, at most 90 days; `format=jsonl|csv`; allowlisted fields; defence-in-depth redaction of secret/content-bearing `details` keys; CSV formula neutralisation; every export audited (`AUDIT_EXPORTED`: range, row count, format only); rate limit 5 exports per admin per hour (`AUDIT_EXPORT_RATE_LIMITED`, DENIED, audited). Executive bound: at most 20,000 events per export (narrow the range otherwise), delivered as a streamed response over an in-memory result set. `AUDITOR` role does not exist yet (D2).
Not implemented: the archival job (events must be at least 24 months old; nothing is that old and no automated deletion exists). The existing `GET /audit` listing is unchanged.

### Implementation queue
Archival job when volume justifies it (moves, never deletes; itself audited).

---

## D4. Support message delivery states and retry (decided 2026-10-31)

A support message is persisted in PostgreSQL in the same transaction as its audit record and notification event, so a message that exists is `SENT`; the server has no half-sent state. "Sending" and "failed" are client transport states: a candidate or administrator retries by resubmitting with the **same idempotency key**, which returns the original message (no duplicate message, event or notification). Reusing a key with a different body is rejected (`idempotency_key_reused_with_different_message`). A separate retry endpoint would add a second path with no benefit and is not built. Evidence: `test_support_notification_events_postgres.py`, `test_support_ticket_notifications_postgres.py`.

## D5. AI task administration (decided 2026-10-31)

Task contracts (input/output schema, validation, allowed tasks) are code-owned and immutable at runtime; this is the capability boundary PRD §49.2 requires ("only capabilities implemented and validated in application code may be controlled through configuration"). Administrators manage tasks through the existing versioned governance lifecycle: `ai.task.<task_id>` configs (system instruction, provider, model, temperature, fallback) are validated against the registry, published immutably, rolled back as new versions, consumed at execution time, and the exact `config_version_id` is recorded on every `AIExecution`. Provider credentials stay in the runtime secret store, never in configuration. No admin API creates new task types or provider adapters; that needs code.

## D6. Administrative intervention catalogue (decided 2026-10-31)

Exactly five operations are interventions: account status change, support ticket reply, assignment and status change, and email-delivery retry. All other admin routes are configuration, source, operations or access governance. The list is enforced by `tests/unit/test_admin_action_inventory.py` and documented in `ADMIN_ACTION_INVENTORY.md`. Application submission is unreachable from the admin surface and from read-only impersonation.

## D7. Read-only inspection session lifetime

Approved by the owner on 14 November 2026: the session limit is 15 minutes (900 seconds). The default is 900 seconds; `JOBPUNTER_ADMIN_IMPERSONATION_TTL_SECONDS` may shorten it to 60–900 seconds, not extend it. Invalid/out-of-range configuration fails closed. Only SUPER_ADMIN and ADMIN can start/view/end these sessions; actor ownership, expiry, explicit exit, grant revocation and read-only enforcement continue to apply. Production settings above 900 must be reduced before deploying this policy.

## D8. Protected system owner

Owner direction on 14 November 2026 supersedes D2 where they conflict: SUPER_ADMIN is the single system owner, with all classified system permissions and exclusive control over granting/revoking built-in ADMIN, SUPPORT_AGENT and OPERATIONS roles. Custom role/permission creation is explicitly deferred until the system matures. Normal ADMIN is no longer permitted to manage access grants.

The owner cannot be revoked, demoted, reassigned, deleted, suspended or deactivated through application/database paths. A partial unique index permits only one owner grant; PostgreSQL triggers keep that grant immutable and its account active, reject grant-table truncation while an owner exists, and prevent removing protection by downgrade while an owner exists. The protected grant is bootstrapped only for an independently verified, existing ACTIVE authentication identity through the operator procedure; never through an unauthenticated endpoint or an email hard-coded into application code. Owner account identity and provisioning evidence belong in the controlled environment's change record, not the repository.

This concerns application ownership; external identity-provider account recovery remains an operator responsibility. No shared deployment or owner assignment is implied by a local test or a PR.
