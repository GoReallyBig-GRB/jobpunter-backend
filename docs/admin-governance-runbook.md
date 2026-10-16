# Admin and system-governance foundation

## Implemented in this branch

- Every admin API request checks a persisted PostgreSQL admin grant and active application-user status.
- Admin grants cannot be created or changed through candidate-facing APIs or these configuration endpoints.
- Configuration drafts are versioned by key. Publishing retires the prior published version.
- Published payloads are not edited in place through these endpoints. Rollback creates a new draft from a prior version and requires a separate publish action.
- Successful draft creation, publication and rollback activation produce durable audit events in the same database transaction.
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

Supported configuration keys are `ai.task.*` and the exact decision-rule key `decision.rules.candidate_evaluation`. Payloads must not contain credentials.

Published `ai.task.*` versions are consumed by the AI generation path for the matching task. Supported fields are `system_instruction` (additional instructions appended after the immutable baseline task policy), `model` (Gemini model identifier), and `temperature` (0 to 1). Provider credentials and provider selection remain server-managed.

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
