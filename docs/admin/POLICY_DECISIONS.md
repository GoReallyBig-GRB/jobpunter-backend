# Admin policy decisions

**Decided:** 2026-10-31, by the product owner (Ijeoma), who delegated these three open decisions with the instruction to choose what is best for the product.
**Status:** DECIDED (policy). Implementation status is stated per decision and is **not** implied by this record.
**Revisable:** yes. A later decision supersedes this one only by editing this file in a reviewed change.
**Sources:** PRD + Technical Spec v1.6 (§49 Admin Control Plane), Admin Backend Implementation Task List (+others), Autonomous Development Protocol v3. These decisions fill gaps those sources leave open; they do not override them.

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

### Decision

1. **MVP launch uses one role, `ADMIN` (full privilege).** The API and database already restrict grants to `ADMIN` and fail closed for any other value; that stays until the enforcement matrix below exists.
2. **Reserved catalogue (not grantable yet):**

| Role | Intended permissions |
|---|---|
| `ADMIN` | All Admin Control Plane operations |
| `SUPPORT_AGENT` | Support tickets and messages; minimized candidate summaries; no configuration, grants, sources, AI governance, audit export or impersonation |
| `AUDITOR` | Read-only audit, health and operations views; no mutations; no candidate content |

3. **A new role becomes grantable only when** (a) every Admin route declares the roles allowed, (b) a route-by-role test matrix proves allowed and denied access for each handler, and (c) every denial is audited. Introducing roles without that matrix is less safe than a single role.
4. **Grant rules:** grants go only to existing active accounts (no invitation flow in the MVP); grant and revoke are performed by an `ADMIN` and audited; the final active administrator cannot be revoked (**implemented**, row-locked); an administrator may revoke their own grant only while another active administrator remains.
5. **No break-glass account and no second-person approval in the MVP.** Recovery is the documented bootstrap runbook (`ADMIN_GRANT_BOOTSTRAP_RUNBOOK.md`); the compensating control is that every grant change is audited.

### Rationale
A single audited role with last-admin protection is enforceable and testable today. Partial role enforcement across 58 handlers would create a false sense of least privilege.

### Implementation queue
Route-by-role declaration and matrix test; this is a **later task**, started when support volume or an operational need justifies a second role (`SUPPORT_AGENT` first).

---

## D3. Audit retention and export

### Decision

1. **Retention:** Admin audit events are append-only (database-enforced) and are kept **indefinitely in the primary store** for the MVP. There is no automated deletion. Before any archival, events must be at least 24 months old, archival must **move** (never destroy) the events, and the archival action must itself be audited.
2. **Export:** Allowed to `ADMIN` only (and `AUDITOR` once that role exists). An export requires an explicit date range of at most 90 days, is streamed (JSON Lines or CSV), and is rate-limited.
3. **Exported fields:** event id, timestamp, actor user id, action, resource type and id, outcome, request id, and the already-sanitized `details`. No secrets, credentials, candidate document content, or message bodies.
4. **Every export is itself audited:** actor, range, row count and format only.
5. **Personal data:** audit events reference users by id and contain no private candidate content, which keeps them minimized. A privacy or legal requirement for deletion or a shorter retention period overrides this default and must be recorded here.

### Implementation status
Not implemented: there is no export endpoint, and no archival job. This is a **later task** (Task List L1.8 and K2), not a gap in current behavior; the existing `GET /audit` listing is unchanged.

### Implementation queue
Export endpoint with date-range enforcement, field allowlist, self-auditing and a test that no secret-bearing field can appear in an export.
