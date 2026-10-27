# Admin and system-governance foundation

## Implemented in this branch

- Every admin API request checks a persisted PostgreSQL admin grant and active application-user status.
- Admin grants cannot be created or changed through candidate-facing APIs or these configuration endpoints.
- Configuration drafts are versioned by key. Publishing retires the prior published version.
- Published payloads are not edited in place through these endpoints. Rollback creates a new draft from a prior version and requires a separate publish action.
- Successful draft creation, publication and rollback activation produce durable audit events in the same database transaction.
- Common secret-bearing payload fields are rejected. Provider credentials remain environment/secret-manager configuration.

## Initial administrator provisioning

There is deliberately no public admin-bootstrap endpoint. Initial provisioning and lockout recovery use a controlled operator/database session. They grant the existing `ADMIN` role only to an already-authenticated, active application user whose Firebase identity has been verified through the approved operator process. Do not change account status as part of grant recovery.

Use a dedicated operator credential and an approved change/recovery ticket. Resolve the verified Firebase UID to exactly one active application user before changing a grant:

    BEGIN;
    LOCK TABLE admin_grants IN SHARE ROW EXCLUSIVE MODE;

    SELECT users.id, users.status
    FROM users
    JOIN identities ON identities.user_id = users.id
    WHERE identities.provider = 'FIREBASE'
      AND identities.provider_subject = '<verified-firebase-uid>'
      AND users.status = 'ACTIVE';

The query must return exactly one row. If it returns no row or more than one row, stop and reconcile the identity mapping before continuing. Then grant or reactivate the row and review its returned values before committing:

    INSERT INTO admin_grants
      (id, user_id, role, active, granted_by_user_id, created_at, revoked_at)
    SELECT gen_random_uuid(), users.id, 'ADMIN', TRUE, NULL, NOW(), NULL
    FROM users
    JOIN identities ON identities.user_id = users.id
    WHERE identities.provider = 'FIREBASE'
      AND identities.provider_subject = '<verified-firebase-uid>'
      AND users.status = 'ACTIVE'
    ON CONFLICT (user_id, role)
    DO UPDATE SET active = TRUE, granted_by_user_id = NULL, revoked_at = NULL
    RETURNING user_id, role, active, granted_by_user_id, created_at, revoked_at;

Confirm that the returned user ID matches the pre-check, the role is `ADMIN`, and the grant is active, then `COMMIT`. Otherwise `ROLLBACK`. `granted_by_user_id` remains null for operator bootstrap/recovery; record the operator, verified UID, returned user ID and ticket in the approved external change log. Do not grant access based on an email or application UUID supplied by an API caller.

## Revocation

    UPDATE admin_grants
    SET active = FALSE, revoked_at = NOW()
    WHERE user_id = '<verified-application-user-uuid>'
      AND role = 'ADMIN';

Revocation takes effect on the next API request because authorization is rechecked every time.

## Configuration contract

Supported configuration keys are `ai.task.*` and the exact decision-rule key `decision.rules.candidate_evaluation`. Payloads must not contain credentials.

Published `ai.task.*` versions are consumed by the AI generation path for the matching task. Supported fields are `system_instruction` (additional instructions appended after the immutable baseline task policy), `provider` (`google` or `openai`), `model` (a model identifier matching that provider), and `temperature` (0 to 1). An optional fallback requires both `fallback_provider` and `fallback_model`, uses the other supported provider, and is attempted once for provider or structured-output failures. Provider selection is controlled by the published server-side task configuration or server environment; credentials remain in environment/secret-manager configuration and cannot be stored in a task payload.

The candidate decision-rule payload currently supports only two soft thresholds:

    {
      "apply_min_score": 80,
      "review_min_score": 35
    }

Both values must be integers from 0 to 100, and `apply_min_score` must be greater than `review_min_score`. Extra fields are rejected. Hard eligibility constraints remain code-owned and cannot be changed by this configuration.

The shared lifecycle provides draft creation, validation, publication, immutable history, and rollback. Rollback activates the previous valid payload as a new published version immediately; it does not rewrite or delete intervening versions. Publication and rollback are audited.

Candidate evaluation consumes the active published decision-rule thresholds and records the config ID, version, and fingerprint in the evaluation input snapshot and policy version. Publishing a new rule version makes evaluations based on the prior version stale. The protected `POST /api/v1/admin/decision-rules/preview` endpoint accepts supplied test inputs and a rule payload, invokes the existing deterministic evaluator, and does not persist candidate evaluations.

Each AI execution records user, task, configuration version, provider/model, status, latency, safe error code, request ID, and timestamps. Candidate prompts and generated outputs are not stored in this telemetry. Do not describe AI governance as complete until server-owned output-contract validation and the remaining controls pass acceptance.

## Remaining acceptance gates

- Add database-level protection against audit-row mutation/deletion; this API only exposes append-only behavior.
- Add two-person approval for high-impact policy changes if required by the finalized PRD.
- Validate task-specific output contracts against server-owned schemas; candidate-supplied schema descriptions are not authoritative output validation.\n- Add token/cost accounting, per-user and global quotas, rate limits, and runaway-task protection.\n- Build an Admin UI over GET /api/v1/admin/ai-executions with privacy-safe filters and operational detail.
- Add stale RUNNING-execution detection and safe recovery for requests interrupted before completion.
- Add PostgreSQL integration tests for migration, concurrent version creation, publishing and rollback.
- Add audit events for denied privileged actions and operational logs that do not leak candidate PII.
- Run the full Alembic chain against a fresh PostgreSQL database and verify ORM/schema parity.
- Confirm the deployment supports gen_random_uuid() before using the provisioning statement.
