# Read-only Admin inspection session API

These endpoints implement a bounded, auditable support-inspection workflow. They do not turn the administrator into the candidate and do not grant candidate credentials.

## Authorization and behavior

All endpoints require the normal trusted Admin authentication and an active ADMIN grant. The backend checks that grant on every request. Session records are scoped to the administrator who started them. The workspace endpoint is read-only and returns the existing minimized Admin user-detail projection; it does not expose document bytes, storage keys, support-message bodies, or notification bodies. No candidate mutation endpoint accepts or uses this session as an authorization context.

The default session lifetime is **900 seconds (15 minutes)**, configurable with JOBPUNTER_ADMIN_IMPERSONATION_TTL_SECONDS. The value must be between 60 and 3600 seconds. This is a conservative implementation default, not a settled product-policy decision; the final value must be recorded in the product decision register before production acceptance.

## Endpoints

### POST /api/v1/admin/impersonation-sessions

Start a session.

Request:

~~~json
{
  "target_user_id": "UUID",
  "reason": "Investigate the reported profile issue."
}
~~~

The reason is required, nonblank and 3–1000 characters. Self-inspection and nonexistent targets are rejected. An administrator may have only one active session for a given target at a time.

Success: 201 Created, with the persisted session record, read_only: true, start time and expiry. Invalid input returns 422; missing target returns 404; an already-active session returns 409.

### GET /api/v1/admin/impersonation-sessions/{session_id}/workspace

Return the current target workspace using canonical user and resource-summary data. The session must belong to the authenticated administrator, remain active and not have expired. This request is audited as both an Admin user-detail read and an impersonation workspace view. An expired or ended session returns 410 Gone; a session owned by another administrator is not disclosed (404).

### POST /api/v1/admin/impersonation-sessions/{session_id}/end

End the session. Repeated exit requests are idempotent. The backend records the end timestamp, outcome and administrator attribution. An expired active session is finalized as EXPIRED.

## Persistence and audit

Migration: 0059_admin_impersonation_sessions.

The session stores administrator, target, reason, status, start, expiry, end and end reason. Start, workspace access, expiry and end are recorded in AdminAuditEvent. A partial unique index prevents concurrent active sessions for the same administrator/target pair.

## Acceptance evidence and remaining limits

The PostgreSQL integration tests cover self-inspection rejection, session creation, minimized workspace access, audited start/view/end, idempotent exit and expiry denial.

The backend provides the session/workspace contract only. The frontend must still implement the persistent impersonation banner and explicit exit control when frontend integration is authorized. Separate administrative interventions are not part of this session and remain independently authorized work. The TTL remains a provisional default pending policy confirmation.
