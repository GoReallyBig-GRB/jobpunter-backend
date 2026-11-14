"""Explicit server-owned role policy for every Admin API route.

Request route templates come from FastAPI routing, never from headers or payloads.
Unclassified routes fail closed. Tests require a policy entry for every route.
"""
from __future__ import annotations

from typing import Any

ADMIN_ROLES = frozenset({"SUPER_ADMIN", "ADMIN", "SUPPORT_AGENT", "OPERATIONS"})
SUPPORT_ROLES = frozenset({"SUPER_ADMIN", "ADMIN", "SUPPORT_AGENT"})
OPERATIONS_ROLES = frozenset({"SUPER_ADMIN", "ADMIN", "OPERATIONS"})
FULL_ADMIN_ONLY = frozenset({"SUPER_ADMIN", "ADMIN"})
OWNER_ONLY = frozenset({"SUPER_ADMIN"})

ROUTE_ROLES: dict[tuple[str, str], frozenset[str]] = {
    ('GET', '/api/v1/admin/ai-executions'): FULL_ADMIN_ONLY,
    ('GET', '/api/v1/admin/audit'): FULL_ADMIN_ONLY,
    ('GET', '/api/v1/admin/audit/export'): FULL_ADMIN_ONLY,
    ('PATCH', '/api/v1/admin/users/{user_id}/status'): FULL_ADMIN_ONLY,
    ('GET', '/api/v1/admin/configs'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/configs'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/configs/{config_id}/publish'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/configs/{config_id}/rollback'): FULL_ADMIN_ONLY,
    ('GET', '/api/v1/admin/smtp/configurations'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/smtp/configurations'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/smtp/configurations/{config_id}/test'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/smtp/configurations/{config_id}/publish'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/smtp/configurations/{config_id}/rollback'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/decision-rules/preview'): FULL_ADMIN_ONLY,
    ('GET', '/api/v1/admin/grants'): OWNER_ONLY,
    ('POST', '/api/v1/admin/grants'): OWNER_ONLY,
    ('POST', '/api/v1/admin/grants/{grant_id}/revoke'): OWNER_ONLY,
    ('GET', '/api/v1/admin/job-resolutions'): FULL_ADMIN_ONLY,
    ('GET', '/api/v1/admin/job-resolutions/{resolution_id}'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/job-resolutions/{resolution_id}/resolve'): FULL_ADMIN_ONLY,
    ('GET', '/api/v1/admin/sources'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/sources'): FULL_ADMIN_ONLY,
    ('GET', '/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration-versions'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration-versions/{version_number}/activate'): FULL_ADMIN_ONLY,
    ('PATCH', '/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/sources/{source_id}/connectors/{connector_id}/configuration-versions/{version_number}/publish'): FULL_ADMIN_ONLY,
    ('PATCH', '/api/v1/admin/sources/{source_id}/permission-review'): FULL_ADMIN_ONLY,
    ('PATCH', '/api/v1/admin/sources/{source_id}/status'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/sources/{source_id}/connectors/{connector_id}/health-check'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/sources/{source_id}/acquisition-runs'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/sources/{source_id}/acquisition-runs'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/sources/{source_id}/acquisition-runs/{run_id}'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/operations/summary'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/operations/workers'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/operations/storage-usage'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/discovery/tools'): OPERATIONS_ROLES,
    ('PATCH', '/api/v1/admin/discovery/tools/{tool_key}/status'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/discovery/source-runs'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/discovery/metrics'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/discovery/sources/{source_id}/limit'): OPERATIONS_ROLES,
    ('PUT', '/api/v1/admin/discovery/sources/{source_id}/limit'): OPERATIONS_ROLES,
    ('DELETE', '/api/v1/admin/discovery/sources/{source_id}/limit'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/discovery/sources/{source_id}/schedule'): OPERATIONS_ROLES,
    ('PUT', '/api/v1/admin/discovery/sources/{source_id}/schedule'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/discovery/sources/{source_id}/schedule/history'): OPERATIONS_ROLES,
    ('PATCH', '/api/v1/admin/discovery/sources/{source_id}/schedule/status'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/discovery/alerts'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/discovery/alerts/{alert_id}/acknowledge'): OPERATIONS_ROLES,
    ('PUT', '/api/v1/admin/discovery/tools/{tool_key}/budget'): OPERATIONS_ROLES,
    ('DELETE', '/api/v1/admin/discovery/tools/{tool_key}/budget'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/operations/work-items'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/operations/work-items/{work_item_id}'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/operations/work-items/{work_item_id}/retry'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/operations/work-items/{work_item_id}/replay'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/operations/work-items/{work_item_id}/cancel'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/operations/outbox-events'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/operations/acquisition-runs/recover-expired'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/operations/outbox-events/{event_id}/retry'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/users'): SUPPORT_ROLES,
    ('GET', '/api/v1/admin/users/{user_id}'): FULL_ADMIN_ONLY,
    ('GET', '/api/v1/admin/notifications/email-deliveries'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/notifications/email-deliveries/{delivery_id}/retry'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/integrations/status'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/impersonation-sessions'): FULL_ADMIN_ONLY,
    ('GET', '/api/v1/admin/impersonation-sessions/{session_id}/workspace'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/impersonation-sessions/{session_id}/end'): FULL_ADMIN_ONLY,
    ('GET', '/api/v1/admin/target-policy/forbidden-rules'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/target-policy/forbidden-rules'): OPERATIONS_ROLES,
    ('PATCH', '/api/v1/admin/target-policy/forbidden-rules/{rule_id}/status'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/integrations/{integration_key}/health-check'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/integrations/observations'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/discovery-targets'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/discovery-targets/{target_id}'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/discovery-targets'): OPERATIONS_ROLES,
    ('PATCH', '/api/v1/admin/discovery-targets/{target_id}/status'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/discovery/strategies'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/discovery/strategies'): FULL_ADMIN_ONLY,
    ('GET', '/api/v1/admin/discovery/strategies/{strategy_id}/versions'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/discovery/strategies/{strategy_id}/validate'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/discovery/strategies/{strategy_id}/publish'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/discovery/strategies/{strategy_id}/rollback'): FULL_ADMIN_ONLY,
    ('POST', '/api/v1/admin/discovery/source-candidates/intake'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/discovery/source-intelligence'): OPERATIONS_ROLES,
    ('PATCH', '/api/v1/admin/discovery/source-intelligence/{profile_id}/review'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/discovery/source-candidates'): OPERATIONS_ROLES,
    ('PATCH', '/api/v1/admin/discovery/source-candidates/{candidate_id}/review'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/discovery/source-candidates/{candidate_id}/acquire'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/discovery/schedules'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/discovery/schedules'): OPERATIONS_ROLES,
    ('PATCH', '/api/v1/admin/discovery/schedules/{schedule_id}/status'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/discovery/schedules/{schedule_id}/runs'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/discovery/runs'): OPERATIONS_ROLES,
    ('POST', '/api/v1/admin/discovery/runs/{run_id}/cancel'): OPERATIONS_ROLES,
    ('GET', '/api/v1/admin/support/tickets'): SUPPORT_ROLES,
    ('GET', '/api/v1/admin/support/tickets/{ticket_id}'): SUPPORT_ROLES,
    ('POST', '/api/v1/admin/support/tickets/{ticket_id}/messages'): SUPPORT_ROLES,
    ('PATCH', '/api/v1/admin/support/tickets/{ticket_id}/status'): SUPPORT_ROLES,
    ('PATCH', '/api/v1/admin/support/tickets/{ticket_id}/assignment'): SUPPORT_ROLES,
    ("GET", "/api/v1/admin/users/{user_id}/support-summary"): SUPPORT_ROLES,
}

ROUTE_ROLES.update({
    ("GET", "/api/v1/admin/discovery/sources/{source_id}/schedule/versions"): OPERATIONS_ROLES,
    ("POST", "/api/v1/admin/discovery/sources/{source_id}/schedule/versions/{config_id}/publish"): OPERATIONS_ROLES,
    ("POST", "/api/v1/admin/discovery/sources/{source_id}/schedule/versions/{config_id}/rollback"): OPERATIONS_ROLES,
})


def permitted_roles(request: Any | None) -> frozenset[str]:
    # Calls without routing context are internal legacy ADMIN-only checks.
    if request is None:
        return FULL_ADMIN_ONLY
    route = request.scope.get("route")
    if route is None:
        return FULL_ADMIN_ONLY
    # FastAPI versions using included-router wrappers retain the defining route's
    # /admin template, while flattened routers include the /api/v1 mount prefix.
    path = route.path
    if path.startswith("/admin/"):
        path = "/api/v1" + path
    return ROUTE_ROLES.get((request.method, path), frozenset())
