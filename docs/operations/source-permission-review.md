# Source permission review contract

## Purpose

Automated source acquisition and connector health checks must fail closed until an authorized administrator records an explicit permission review. Approval is an auditable operational decision, not proof that an external source's terms have been independently verified by the application.

## Approval request

`PATCH /api/v1/admin/sources/{source_id}/permission-review`

All requests require trusted server-side admin authorization.

Supported status values are `APPROVED`, `PENDING`, and `REJECTED`. The review reference is retained as `permission_review_reference`. An `APPROVED` request must include nonblank:

- `access_method`: the reviewed acquisition method, such as a public API or public job-board endpoint.
- `terms_reference`: a URL or internal reference to the applicable terms/permission evidence.
- `rate_limit_policy`: the reviewed rate/scheduling policy, including an explicit statement when the provider publishes no numeric limit.
- `reference`: the auditable review record/reference.

Optional fields are `rate_limit_reference` and `review_notes`. Unknown request fields and whitespace-only review fields are rejected. An incomplete approval receives HTTP 422 and must not change the source status or create a successful review audit event.

## Persistence and audit

The current review details are stored under the source's `configuration.permission_review` object and exposed as the dedicated `permission_review_details` field in the source registry response. The raw source configuration is not returned by this response.

Each review change writes an administrative audit event with the actor, request ID, previous review state/details, new status/reference/details, review timestamp where applicable, and resulting operational state. Rejection/revocation clears `permission_reviewed_at`, moves the source to `RESTRICTED`, and sets `PERMISSION_REVIEW_REQUIRED`. Previously recorded review details are retained for audit/history, but do not authorize acquisition.

## Enforcement boundary

The backend blocks automated acquisition and connector health checks when review approval is absent or revoked. This gate is distinct from connector health: a configured connector is not evidence of permission or operational availability.

**Important limitation:** `rate_limit_policy` is reviewed metadata, not a distributed rate limiter. Actual rate/scheduling limits must also be configured and enforced by the acquisition/worker path. Do not mark a source's real-world terms, permission, numeric rate limits, or deployed health as verified merely because a review record exists. Those require source-specific evidence and authorized runtime checks.
