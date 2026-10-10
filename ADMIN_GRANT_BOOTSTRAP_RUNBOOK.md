# First administrator bootstrap and recovery runbook

Status: operator procedure for the current backend schema. This runbook has not been executed against a staging or production environment.

## Security boundary

- There is deliberately no unauthenticated HTTP endpoint for creating the first administrator.
- Normal grant creation, reactivation, and revocation must use the authenticated `/api/v1/admin/grants` endpoints. Those changes create application audit events.
- An operator bootstrap is an out-of-band database operation. The current `admin_audit_events.actor_user_id` column is non-null and requires a user actor, so a bootstrap performed before any administrator exists cannot be represented truthfully as a normal user-authored application audit event. Record the bootstrap in the approved infrastructure/change-management audit system. Do not attribute it to the target user.
- Use the deployment's approved secret manager and privileged database access. Do not place database credentials, Firebase tokens, or service-account keys in source control, command history, or tickets.

## Preconditions

1. Confirm the target environment and database before making changes:
   ```sql
   SELECT current_database(), current_user;
   ```
2. The intended administrator must have signed in to the correct Firebase project at least once, so the backend has created their local user and identity mapping.
3. Verify the Firebase UID through the trusted Firebase administration console, then resolve the local user:
   ```sql
   SELECT u.id AS user_id, u.status, i.provider, i.provider_subject
   FROM users AS u
   JOIN identities AS i ON i.user_id = u.id
   WHERE i.provider = 'FIREBASE'
     AND i.provider_subject = '<VERIFIED_FIREBASE_UID>';
   ```
   Continue only if this returns exactly one row, the provider subject is the verified UID, and the user status is `ACTIVE`. Record the returned `user_id`; do not guess it or match by display name.
4. Check active administrator grants:
   ```sql
   SELECT id, user_id, role, active, granted_by_user_id, created_at, revoked_at
   FROM admin_grants
   WHERE role = 'ADMIN' AND active IS TRUE
   ORDER BY created_at, id;
   ```
   For first bootstrap or full lockout recovery, the expected active count is zero. If any active grant exists and its holder can authenticate, use the admin API instead. If an active grant exists but no holder can authenticate, treat this as a recovery incident and record explicit approval in the change record before proceeding.

## First bootstrap or full lockout recovery

Use a transaction in an approved SQL client. Replace `<VERIFIED_USER_UUID>` with the UUID returned by the identity lookup. Keep the preflight and mutation in the same transaction. The advisory lock serializes operators following this runbook; it does not replace the preflight checks.

```sql
BEGIN;

SELECT pg_advisory_xact_lock(748235109);

SELECT id, status
FROM users
WHERE id = '<VERIFIED_USER_UUID>'
FOR UPDATE;

SELECT COUNT(*) AS active_admin_count
FROM admin_grants
WHERE role = 'ADMIN' AND active IS TRUE;

-- STOP and ROLLBACK if the user is not ACTIVE, the identity mapping
-- was not verified, or the active grant count is not zero for a
-- first-admin/full-lockout bootstrap.

INSERT INTO admin_grants
    (id, user_id, role, active, granted_by_user_id, created_at, revoked_at)
VALUES
    (gen_random_uuid(), '<VERIFIED_USER_UUID>', 'ADMIN', TRUE, NULL, now(), NULL)
ON CONFLICT (user_id, role) DO UPDATE
    SET active = TRUE,
        granted_by_user_id = NULL,
        revoked_at = NULL
    WHERE admin_grants.active IS FALSE
RETURNING id, user_id, role, active, granted_by_user_id, created_at, revoked_at;

-- Confirm exactly one active grant exists for the intended user.
SELECT id, user_id, role, active, granted_by_user_id, created_at, revoked_at
FROM admin_grants
WHERE user_id = '<VERIFIED_USER_UUID>' AND role = 'ADMIN';

COMMIT;
```

If the insert/update returns no row, or any check is unexpected, issue `ROLLBACK` instead of `COMMIT`. Do not use this procedure for routine grants while an authenticated administrator is available.

## Required external change record

Before closing the change, record all of the following in the approved operator/change-management system:

- Operator identity and approval/change-ticket reference
- Environment and database identifier (never the database password)
- Verified Firebase UID and resolved local user UUID
- UTC start/end time
- Active-grant count before and after the transaction
- The returned grant UUID and verification result
- Why out-of-band bootstrap/recovery was required

The application audit API will not show this initial out-of-band operation in `admin_audit_events`. This is a known auditability limitation of the current schema, not a successful application-audit event.

## Post-change verification

1. Sign in as the intended administrator using the configured Firebase project.
2. Call `GET /api/v1/admin/grants`; verify the intended user has one active `ADMIN` grant.
3. Call `GET /api/v1/admin/audit`; verify it is protected and returns application audit events. Do not expect the out-of-band bootstrap to appear there.
4. If this is a new environment, use the authenticated API to grant a second active administrator. Verify that `ADMIN_GRANT_CREATED` appears in the application audit feed.
5. Record the API verification results in the external change record.

## Recovery and revocation rules

- If at least one reachable administrator remains, use the authenticated grant API to create or reactivate another grant. Do not edit grant rows directly for routine work.
- If all administrators are inactive, repeat the out-of-band recovery procedure after verifying the replacement user's Firebase identity and active user status.
- If a sole active administrator is incorrect but can authenticate, grant the correct user first, verify the new administrator, then revoke the incorrect grant through the API.
- Never revoke or deactivate the final active administrator through SQL as a routine fix. The API intentionally prevents final-admin revocation.
- If identity verification, target-user status, or the active-grant count is ambiguous, stop and escalate to the database/security operator. Do not improvise by matching names or email strings.

## Remaining limitation

The procedure is documented but not yet exercised in a staging environment. The backend also lacks a first-class system/operator actor type in its application audit schema, so out-of-band bootstrap audit remains external. Close these gaps only with a reviewed operational test and an explicit audit-schema design decision.
