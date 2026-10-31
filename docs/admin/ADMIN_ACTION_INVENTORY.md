# Admin action inventory and permission matrix

**Date:** 2026-10-31 · Task List L3.2, H2.1. Enforced by `tests/unit/test_admin_action_inventory.py`: a new mutating admin route fails CI until it is classified here and in the test.

**Permission:** every route below requires an active `ADMIN` grant (`_require_admin`, fail-closed; policy D2). Audit: every route is covered by the failure-outcome matrix test and success auditing per `A0_CLOSURE_STATUS.md` (A0-CLOSE-04). Resource scope: candidate private content (documents, evidence, message bodies) is never returned by these routes.

## Executive decision: intervention catalogue (H2.1)
Administrative interventions that affect candidate-visible state are exactly the five routes classified INTERVENTION: account status, support-ticket reply/assignment/status, and email-delivery retry. Each runs through its canonical service, is attributed to the administrator (never the candidate), and is audited. No other intervention exists, and none will be added without a product requirement. Application submission is unreachable from the admin surface and from read-only impersonation (impersonation exposes only a GET workspace).

| Route | Category | Purpose |
|---|---|---|
| `POST /api/v1/admin/configs` | CONFIGURATION | Governance config draft |
| `POST /api/v1/admin/configs/{config_id}/publish` | CONFIGURATION | Publish governed config |
| `POST /api/v1/admin/configs/{config_id}/rollback` | CONFIGURATION | Rollback as new version |
| `POST /api/v1/admin/decision-rules/preview` | READ_ONLY_PREVIEW | Side-effect-free rule preview; no mutation audit by design |
| `POST /api/v1/admin/discovery-targets` | DISCOVERY_POLICY | Register discovery target |
| `PATCH /api/v1/admin/discovery-targets/{target_id}/status` | DISCOVERY_POLICY | Target lifecycle |
| `POST /api/v1/admin/grants` | ACCESS_GOVERNANCE | Grant/reactivate ADMIN |
| `POST /api/v1/admin/grants/{grant_id}/revoke` | ACCESS_GOVERNANCE | Revoke ADMIN; last-admin protected |
| `POST /api/v1/admin/impersonation-sessions` | IMPERSONATION | Start read-only session (reason required) |
| `POST /api/v1/admin/impersonation-sessions/{session_id}/end` | IMPERSONATION | End session |
| `POST /api/v1/admin/integrations/{integration_key}/health-check` | INTEGRATION | Observed health check |
| `POST /api/v1/admin/job-resolutions/{resolution_id}/resolve` | SHARED_POOL_REVIEW | Resolve ambiguous job identity (shared pool, not candidate-private) |
| `POST /api/v1/admin/notifications/email-deliveries/{delivery_id}/retry` | INTERVENTION | Retry a failed email delivery |
| `POST /api/v1/admin/operations/acquisition-runs/recover-expired` | OPERATIONS_RECOVERY | Recover expired acquisition runs |
| `POST /api/v1/admin/operations/outbox-events/{event_id}/retry` | OPERATIONS_RECOVERY | Retry dead outbox event |
| `POST /api/v1/admin/operations/work-items/{work_item_id}/cancel` | OPERATIONS_RECOVERY | Cancel work item |
| `POST /api/v1/admin/operations/work-items/{work_item_id}/replay` | OPERATIONS_RECOVERY | Replay work item |
| `POST /api/v1/admin/operations/work-items/{work_item_id}/retry` | OPERATIONS_RECOVERY | Retry work item |
| `POST /api/v1/admin/smtp/configurations` | CONFIGURATION | SMTP draft |
| `POST /api/v1/admin/smtp/configurations/{config_id}/publish` | CONFIGURATION | SMTP publish (test-before-publish) |
| `POST /api/v1/admin/smtp/configurations/{config_id}/rollback` | CONFIGURATION | SMTP rollback as new draft |
| `POST /api/v1/admin/smtp/configurations/{config_id}/test` | INTEGRATION | SMTP connectivity test |
| `POST /api/v1/admin/sources` | SOURCE_OPERATIONS | Register source |
| `POST /api/v1/admin/sources/{source_id}/acquisition-runs` | SOURCE_OPERATIONS | Run acquisition |
| `PATCH /api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration` | SOURCE_OPERATIONS | Connector configuration draft |
| `POST /api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration-versions/{version_number}/activate` | SOURCE_OPERATIONS | Activate connector config version |
| `POST /api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration-versions/{version_number}/publish` | SOURCE_OPERATIONS | Publish connector config version |
| `POST /api/v1/admin/sources/{source_id}/connectors/{connector_id}/health-check` | SOURCE_OPERATIONS | Connector health check |
| `PATCH /api/v1/admin/sources/{source_id}/permission-review` | SOURCE_OPERATIONS | Source permission review |
| `PATCH /api/v1/admin/sources/{source_id}/status` | SOURCE_OPERATIONS | Source lifecycle |
| `PATCH /api/v1/admin/support/tickets/{ticket_id}/assignment` | INTERVENTION | Assign/reassign support ticket |
| `POST /api/v1/admin/support/tickets/{ticket_id}/messages` | INTERVENTION | Administrator reply (attributed to the admin) |
| `PATCH /api/v1/admin/support/tickets/{ticket_id}/status` | INTERVENTION | Resolve/close support ticket |
| `POST /api/v1/admin/target-policy/forbidden-rules` | DISCOVERY_POLICY | Create forbidden rule |
| `PATCH /api/v1/admin/target-policy/forbidden-rules/{rule_id}/status` | DISCOVERY_POLICY | Forbidden rule lifecycle |
| `PATCH /api/v1/admin/users/{user_id}/status` | INTERVENTION | Account status ACTIVE/SUSPENDED/DEACTIVATED |
