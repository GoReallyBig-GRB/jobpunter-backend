# Read-only Admin inspection session API

These endpoints implement a bounded, auditable support-inspection workflow. They do not turn the administrator into the candidate and do not grant candidate credentials.

## Authorization and behavior

All endpoints require the normal trusted Admin authentication and an active ADMIN grant. The backend checks that grant on every request. Session records are scoped to the administrator who started them. The workspace endpoint is read-only and returns the existing minimized Admin user-detail projection; it does not expose document bytes, storage keys, support-message bodies, or notification bodies. No candidate mutation endpoint accepts or uses this session as an authorization context.

The default session lifetime is **900 seconds (15 minutes)**, configurable with JOBPUNTER_ADMIN_IMPERSONATION_TTL_SECONDS. The value must be between 60 and 900 seconds. The owner approved the 15-minute maximum on 14 November 2026 (policy D7); configuration may shorten it, not extend it.

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

The backend provides the session/workspace contract only. The frontend must still implement the persistent impersonation banner and explicit exit control when frontend integration is authorized. Separate administrative interventions are not part of this session and remain independently authorized work. The 15-minute maximum is approved in policy D7; deployed acceptance remains separate.

## Frontend contract (banner and exit)

This section specifies what the Admin frontend must do when frontend integration is authorized. It adds no backend behavior; each statement below is a property of the endpoints above, not a UI guarantee.

**Banner data.** The persistent banner reads only the session record returned by `POST /impersonation-sessions`: `id`, `target_user_id`, `reason`, `status`, `started_at`, `expires_at` and `read_only` (always `true`). The record contains no target name or email; if the banner shows one, it must come from the minimized workspace projection returned by `GET .../workspace`, not from a separate candidate lookup. The banner must be visible on every screen while `status` is active, and must show the remaining time derived from `expires_at` (server time is authoritative; the client clock is display only).

**No client-side authority.** The banner and the exit button are presentation. The backend re-checks the administrator's active grant, session ownership and expiry on every `workspace` request, so hiding or removing the banner cannot extend or widen access. Do not store `session_id` anywhere that could be reused as a credential for candidate endpoints: no candidate endpoint accepts it, and any attempt is denied.

**Exit.** The exit control calls `POST .../{session_id}/end`. The call is idempotent, so retry on network failure is safe and a second click is harmless. Treat the session as ended only after a success response; if the outcome is unknown (timeout), show "unknown" and let the administrator retry rather than claiming it ended.

**State handling.**

| Response | Meaning | Frontend behavior |
| --- | --- | --- |
| 201 on start | Session active, read-only | Show banner |
| 409 on start | An active session already exists for this admin and target | Offer to resume the existing session; do not create another |
| 404 on workspace or end | Session not found or owned by another administrator (not disclosed) | Clear the banner; show not found |
| 410 on workspace | Session ended or expired | Clear the banner; show "session expired" and offer a new start with a reason |
| 401 or 403 | Authentication lost or the ADMIN grant was revoked | Clear the banner and send the administrator to sign-in; access is already denied server-side |
| 422 on start | Missing or invalid reason (3 to 1000 characters) or self-inspection | Show field-level validation |

**Expiry.** Do not run a background refresh that extends a session; there is no extension endpoint. When `expires_at` passes, the next `workspace` call returns 410 and the backend finalizes the session as EXPIRED. The 900-second default is provisional pending policy confirmation (see POLICY_DECISIONS).

**Not in scope.** Interventions (account changes made outside the read-only session) are separate, individually authorized operations and must not be offered from inside the impersonation banner as if they were part of the session. Application submission is never available.
