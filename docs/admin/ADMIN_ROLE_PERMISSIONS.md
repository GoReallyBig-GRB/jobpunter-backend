# Admin role permissions

Owner-approved scope on 14 November 2026: SUPER_ADMIN has system-wide control and alone manages the built-in ADMIN, SUPPORT_AGENT and OPERATIONS grants. Custom roles and permission definitions are deferred. The single owner cannot be removed, demoted, reassigned or suspended; PostgreSQL enforces owner protection in migration 0083. Provisioning requires verified identity and the controlled owner bootstrap procedure.

Role checks use active server-side grants on active accounts and the routed method/template, never client-supplied role claims. Unclassified routes deny everyone. The matrix is exhaustive and covered by the role/denial-audit tests. SUPPORT_AGENT receives ticket workflow, bounded directory and identity/status-only support summaries; OPERATIONS receives approved source/discovery and recovery controls without candidate/support content or credentials. Inspection stays full-administrator-only and capped at 15 minutes. Normal ADMIN cannot grant or revoke roles.

| Method | Route | Roles |
|---|---|---|
| GET | `/api/v1/admin/ai-executions` | ADMIN, SUPER_ADMIN |
| GET | `/api/v1/admin/audit` | ADMIN, SUPER_ADMIN |
| GET | `/api/v1/admin/audit/export` | ADMIN, SUPER_ADMIN |
| GET | `/api/v1/admin/configs` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/configs` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/configs/{config_id}/publish` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/configs/{config_id}/rollback` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/decision-rules/preview` | ADMIN, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery-targets` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/discovery-targets` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery-targets/{target_id}` | ADMIN, OPERATIONS, SUPER_ADMIN |
| PATCH | `/api/v1/admin/discovery-targets/{target_id}/status` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery/alerts` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/discovery/alerts/{alert_id}/acknowledge` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery/metrics` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery/runs` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/discovery/runs/{run_id}/cancel` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery/schedules` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/discovery/schedules` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/discovery/schedules/{schedule_id}/runs` | ADMIN, OPERATIONS, SUPER_ADMIN |
| PATCH | `/api/v1/admin/discovery/schedules/{schedule_id}/status` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery/source-candidates` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/discovery/source-candidates/intake` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/discovery/source-candidates/{candidate_id}/acquire` | ADMIN, OPERATIONS, SUPER_ADMIN |
| PATCH | `/api/v1/admin/discovery/source-candidates/{candidate_id}/review` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery/source-intelligence` | ADMIN, OPERATIONS, SUPER_ADMIN |
| PATCH | `/api/v1/admin/discovery/source-intelligence/{profile_id}/review` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery/source-runs` | ADMIN, OPERATIONS, SUPER_ADMIN |
| DELETE | `/api/v1/admin/discovery/sources/{source_id}/limit` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery/sources/{source_id}/limit` | ADMIN, OPERATIONS, SUPER_ADMIN |
| PUT | `/api/v1/admin/discovery/sources/{source_id}/limit` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery/sources/{source_id}/schedule` | ADMIN, OPERATIONS, SUPER_ADMIN |
| PUT | `/api/v1/admin/discovery/sources/{source_id}/schedule` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery/sources/{source_id}/schedule/history` | ADMIN, OPERATIONS, SUPER_ADMIN |
| PATCH | `/api/v1/admin/discovery/sources/{source_id}/schedule/status` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery/sources/{source_id}/schedule/versions` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/discovery/sources/{source_id}/schedule/versions/{config_id}/publish` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/discovery/sources/{source_id}/schedule/versions/{config_id}/rollback` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery/strategies` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/discovery/strategies` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/discovery/strategies/{strategy_id}/publish` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/discovery/strategies/{strategy_id}/rollback` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/discovery/strategies/{strategy_id}/validate` | ADMIN, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery/strategies/{strategy_id}/versions` | ADMIN, SUPER_ADMIN |
| GET | `/api/v1/admin/discovery/tools` | ADMIN, OPERATIONS, SUPER_ADMIN |
| DELETE | `/api/v1/admin/discovery/tools/{tool_key}/budget` | ADMIN, OPERATIONS, SUPER_ADMIN |
| PUT | `/api/v1/admin/discovery/tools/{tool_key}/budget` | ADMIN, OPERATIONS, SUPER_ADMIN |
| PATCH | `/api/v1/admin/discovery/tools/{tool_key}/status` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/grants` | SUPER_ADMIN |
| POST | `/api/v1/admin/grants` | SUPER_ADMIN |
| POST | `/api/v1/admin/grants/{grant_id}/revoke` | SUPER_ADMIN |
| POST | `/api/v1/admin/impersonation-sessions` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/impersonation-sessions/{session_id}/end` | ADMIN, SUPER_ADMIN |
| GET | `/api/v1/admin/impersonation-sessions/{session_id}/workspace` | ADMIN, SUPER_ADMIN |
| GET | `/api/v1/admin/integrations/observations` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/integrations/status` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/integrations/{integration_key}/health-check` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/job-resolutions` | ADMIN, SUPER_ADMIN |
| GET | `/api/v1/admin/job-resolutions/{resolution_id}` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/job-resolutions/{resolution_id}/resolve` | ADMIN, SUPER_ADMIN |
| GET | `/api/v1/admin/notifications/email-deliveries` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/notifications/email-deliveries/{delivery_id}/retry` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/operations/acquisition-runs/recover-expired` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/operations/outbox-events` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/operations/outbox-events/{event_id}/retry` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/operations/storage-usage` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/operations/summary` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/operations/work-items` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/operations/work-items/{work_item_id}` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/operations/work-items/{work_item_id}/cancel` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/operations/work-items/{work_item_id}/replay` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/operations/work-items/{work_item_id}/retry` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/operations/workers` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/smtp/configurations` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/smtp/configurations` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/smtp/configurations/{config_id}/publish` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/smtp/configurations/{config_id}/rollback` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/smtp/configurations/{config_id}/test` | ADMIN, SUPER_ADMIN |
| GET | `/api/v1/admin/sources` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/sources` | ADMIN, SUPER_ADMIN |
| GET | `/api/v1/admin/sources/{source_id}/acquisition-runs` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/sources/{source_id}/acquisition-runs` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/sources/{source_id}/acquisition-runs/{run_id}` | ADMIN, OPERATIONS, SUPER_ADMIN |
| PATCH | `/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration` | ADMIN, SUPER_ADMIN |
| GET | `/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration-versions` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration-versions/{version_number}/activate` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration-versions/{version_number}/publish` | ADMIN, SUPER_ADMIN |
| POST | `/api/v1/admin/sources/{source_id}/connectors/{connector_id}/health-check` | ADMIN, OPERATIONS, SUPER_ADMIN |
| PATCH | `/api/v1/admin/sources/{source_id}/permission-review` | ADMIN, SUPER_ADMIN |
| PATCH | `/api/v1/admin/sources/{source_id}/status` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/support/tickets` | ADMIN, SUPER_ADMIN, SUPPORT_AGENT |
| GET | `/api/v1/admin/support/tickets/{ticket_id}` | ADMIN, SUPER_ADMIN, SUPPORT_AGENT |
| PATCH | `/api/v1/admin/support/tickets/{ticket_id}/assignment` | ADMIN, SUPER_ADMIN, SUPPORT_AGENT |
| POST | `/api/v1/admin/support/tickets/{ticket_id}/messages` | ADMIN, SUPER_ADMIN, SUPPORT_AGENT |
| PATCH | `/api/v1/admin/support/tickets/{ticket_id}/status` | ADMIN, SUPER_ADMIN, SUPPORT_AGENT |
| GET | `/api/v1/admin/target-policy/forbidden-rules` | ADMIN, OPERATIONS, SUPER_ADMIN |
| POST | `/api/v1/admin/target-policy/forbidden-rules` | ADMIN, OPERATIONS, SUPER_ADMIN |
| PATCH | `/api/v1/admin/target-policy/forbidden-rules/{rule_id}/status` | ADMIN, OPERATIONS, SUPER_ADMIN |
| GET | `/api/v1/admin/users` | ADMIN, SUPER_ADMIN, SUPPORT_AGENT |
| GET | `/api/v1/admin/users/{user_id}` | ADMIN, SUPER_ADMIN |
| PATCH | `/api/v1/admin/users/{user_id}/status` | ADMIN, SUPER_ADMIN |
| GET | `/api/v1/admin/users/{user_id}/support-summary` | ADMIN, SUPER_ADMIN, SUPPORT_AGENT |
