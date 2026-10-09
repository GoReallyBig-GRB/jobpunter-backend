# Admin and system-governance foundation

## Implemented in this branch

- Every admin API request checks a persisted PostgreSQL admin grant and active application-user status.
- Admin grants cannot be created or changed through candidate-facing APIs or these configuration endpoints.
- Configuration drafts are versioned by key. Publishing retires the prior published version.
- Published payloads are not edited in place through these endpoints. Rollback creates a new draft from a prior version and requires a separate publish action.
- Successful draft creation, publication and rollback-draft creation produce durable audit events in the same database transaction.
- Common secret-bearing payload fields are rejected. Provider credentials remain environment/secret-manager configuration.

## Initial administrator provisioning

There is deliberately no public admin-bootstrap endpoint. Apply the migration, then use a controlled operator/database session to grant access to an already-authenticated, active application user. Verify the Firebase UID-to-user mapping first.

Example SQL; replace the UUID only after verifying the user through the approved operator process:

    INSERT INTO admin_grants
      (id, user_id, role, active, granted_by_user_id, created_at, revoked_at)
    SELECT gen_random_uuid(), users.id, 'ADMIN', TRUE, NULL, NOW(), NULL
    FROM users
    WHERE users.id = '<verified-application-user-uuid>'
      AND users.status = 'ACTIVE'
    ON CONFLICT (user_id, role)
    DO UPDATE SET active = TRUE, revoked_at = NULL;

Do not grant access based on an email or UUID supplied by an API caller. Record the operator/change ticket outside the application.

## Revocation

    UPDATE admin_grants
    SET active = FALSE, revoked_at = NOW()
    WHERE user_id = '<verified-application-user-uuid>'
      AND role = 'ADMIN';

Revocation takes effect on the next API request because authorization is rechecked every time.

## Configuration contract

Allowed configuration key namespaces are ai.task.* and system.*. Payloads must not contain credentials. Published ai.task.* versions are now consumed by the AI generation path for the matching task. Supported fields are system_instruction (additional instructions appended after the immutable baseline task policy), model (Gemini model identifier), and temperature (0 to 1). Provider credentials and provider selection remain server-managed. Each execution records user, task, configuration version, provider/model, status, latency, safe error code, request ID, and timestamps. Candidate prompts and generated outputs are not stored in this telemetry. Do not describe AI governance as complete until server-owned output-contract validation and the remaining controls pass acceptance.

## Remaining acceptance gates

- Add database-level protection against audit-row mutation/deletion; this API only exposes append-only behavior.
- Add two-person approval for high-impact policy changes if required by the finalized PRD.
- Validate task-specific output contracts against server-owned schemas; candidate-supplied schema descriptions are not authoritative output validation.\n- Add token/cost accounting, per-user and global quotas, rate limits, and runaway-task protection.\n- Add a protected execution-history endpoint and Admin UI with privacy-safe filters and operational detail.
- Add PostgreSQL integration tests for migration, concurrent version creation, publishing and rollback.
- Add audit events for denied privileged actions and operational logs that do not leak candidate PII.
- Run the full Alembic chain against a fresh PostgreSQL database and verify ORM/schema parity.
- Confirm the deployment supports gen_random_uuid() before using the provisioning statement.
