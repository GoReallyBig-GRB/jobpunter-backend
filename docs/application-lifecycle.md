# Application lifecycle and audit contract

**Status:** Backend contract for Batch 5  
**Specification basis:** JobPunter PRD + Technical Spec v1.5, especially the application pipeline and Technical Specification sections 15–16.

## Canonical candidate-facing states

The backend uses the technical specification's explicit candidate-facing state model:

- `PLANNED`
- `APPROVED`
- `SUBMITTED`
- `INTERVIEW`
- `OFFER`
- `ACCEPTED`
- `REJECTED`
- `WITHDRAWN`
- `CLOSED`

Allowed transitions:

| Current state | Allowed next states |
| --- | --- |
| `PLANNED` | `APPROVED`, `WITHDRAWN`, `CLOSED` |
| `APPROVED` | `SUBMITTED`, `WITHDRAWN`, `CLOSED` |
| `SUBMITTED` | `INTERVIEW`, `OFFER`, `REJECTED`, `WITHDRAWN`, `CLOSED` |
| `INTERVIEW` | `OFFER`, `REJECTED`, `WITHDRAWN`, `CLOSED` |
| `OFFER` | `ACCEPTED`, `REJECTED`, `WITHDRAWN`, `CLOSED` |
| `ACCEPTED`, `REJECTED`, `WITHDRAWN`, `CLOSED` | No further transitions |

The PRD's higher-level flow also names `SCREENING`, `INTERVIEW INVITED`, `WON`, `LOST`, and `ARCHIVED`. To resolve that vocabulary against the more explicit technical state model, `SCREENING` and `INTERVIEW INVITED` are not application states; a future activity/event contract may record them. `WON` corresponds to `ACCEPTED`. A lost application is represented by the specific terminal state `REJECTED`, `WITHDRAWN`, or `CLOSED`, not a generic `LOST` state. `ARCHIVED` is not a canonical lifecycle state in this backend contract.

## Who may assert lifecycle changes

The current MVP supports authenticated candidate/user actions only. The transition service records actor type `USER` and the authenticated user ID. System-originated transitions cannot assert submission, interview, offer, acceptance, rejection, withdrawal, or closure. A verified integration path is not implemented.

Creating an application from a qualified Punt emits `APPLICATION_CREATED` with the initial `PLANNED` state. Candidate transitions emit `APPLICATION_STATUS_CHANGED`. These persisted records do not submit an application to an employer. They are stored in `application_events`, not emitted as outbox messages, so no outbox handlers are registered for these event types in this batch. If they are later routed through the outbox, handlers and handler-level idempotency must be added and tested.

## Approval and submission gates

Before moving an application to `APPROVED`, the backend rechecks:

- The job is active/open and its stored freshness status is `FRESH` or `CURRENT`.
- The latest job description is marked `COMPLETE` and contains substantive content.
- The current application URL has a successful, URL-matched verification within the existing 24-hour verification window.
- The latest application package exists and is `APPROVED`.
- The package still passes readiness checks, and its snapshot refers to the current job and latest job-description version.

Before recording `SUBMITTED`, the application must already be `APPROVED`; the latest package must still be approved and pass readiness checks. Submission records the package ID and version used.

## Transition request and history

`POST /api/v1/candidate/applications/{application_id}/transition` accepts `target_status`, `expected_revision`, required `reason` (3–1000 characters), and optional `notes` (up to 5000 characters).

A stale revision returns a conflict. Invalid transitions are rejected without changing the application or writing a transition event.

`GET /api/v1/candidate/applications/{application_id}/events` returns candidate-owned audit history for that application.

## Append-only audit projection

`audit_events` is the unified, append-only audit projection. PostgreSQL triggers mirror application lifecycle events and existing admin audit records into it, and the migration backfills records that existed before this table was added. Database triggers reject UPDATE and DELETE against `audit_events`. The application and admin source tables remain operational records; the unified projection is the immutable audit history.

Audit records retain event/resource identity, actor type and ID, status transition where relevant, reason, notes, details, request ID where available, and timestamp. Existing admin audit entries are mirrored so privileged actions already audited by the backend appear in the same history store.
