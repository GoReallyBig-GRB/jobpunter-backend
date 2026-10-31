"""Every mutating admin route, classified. A new route fails this test until it is classified here.

Executive decision (H2.1, 2026-10-31): the administrative INTERVENTION catalogue is exactly the routes
classified INTERVENTION below (account status, support-ticket handling, email-delivery retry). Each is
admin-authorized, reason/audit-attributed to the administrator and runs through canonical services.
No other candidate-affecting action exists; application submission is not reachable from the admin
surface or impersonation.
"""
from app.api.v1 import admin as admin_api
from app.api.v1 import support as support_api

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

INVENTORY = {('PATCH', '/admin/discovery-targets/{target_id}/status'): ('DISCOVERY_POLICY', 'Target lifecycle'),
 ('PATCH', '/admin/sources/{source_id}/connectors/{connector_id}/configuration'): ('SOURCE_OPERATIONS',
                                                                                   'Connector configuration '
                                                                                   'draft'),
 ('PATCH', '/admin/sources/{source_id}/permission-review'): ('SOURCE_OPERATIONS', 'Source permission review'),
 ('PATCH', '/admin/sources/{source_id}/status'): ('SOURCE_OPERATIONS', 'Source lifecycle'),
 ('PATCH', '/admin/support/tickets/{ticket_id}/assignment'): ('INTERVENTION',
                                                              'Assign/reassign support ticket'),
 ('PATCH', '/admin/support/tickets/{ticket_id}/status'): ('INTERVENTION', 'Resolve/close support ticket'),
 ('PATCH', '/admin/target-policy/forbidden-rules/{rule_id}/status'): ('DISCOVERY_POLICY',
                                                                      'Forbidden rule lifecycle'),
 ('PATCH', '/admin/users/{user_id}/status'): ('INTERVENTION', 'Account status ACTIVE/SUSPENDED/DEACTIVATED'),
 ('POST', '/admin/configs'): ('CONFIGURATION', 'Governance config draft'),
 ('POST', '/admin/configs/{config_id}/publish'): ('CONFIGURATION', 'Publish governed config'),
 ('POST', '/admin/configs/{config_id}/rollback'): ('CONFIGURATION', 'Rollback as new version'),
 ('POST', '/admin/decision-rules/preview'): ('READ_ONLY_PREVIEW',
                                             'Side-effect-free rule preview; no mutation audit by design'),
 ('POST', '/admin/discovery-targets'): ('DISCOVERY_POLICY', 'Register discovery target'),
 ('POST', '/admin/grants'): ('ACCESS_GOVERNANCE', 'Grant/reactivate ADMIN'),
 ('POST', '/admin/grants/{grant_id}/revoke'): ('ACCESS_GOVERNANCE', 'Revoke ADMIN; last-admin protected'),
 ('POST', '/admin/impersonation-sessions'): ('IMPERSONATION', 'Start read-only session (reason required)'),
 ('POST', '/admin/impersonation-sessions/{session_id}/end'): ('IMPERSONATION', 'End session'),
 ('POST', '/admin/integrations/{integration_key}/health-check'): ('INTEGRATION', 'Observed health check'),
 ('POST', '/admin/job-resolutions/{resolution_id}/resolve'): ('SHARED_POOL_REVIEW',
                                                              'Resolve ambiguous job identity (shared pool, '
                                                              'not candidate-private)'),
 ('POST', '/admin/notifications/email-deliveries/{delivery_id}/retry'): ('INTERVENTION',
                                                                         'Retry a failed email delivery'),
 ('POST', '/admin/operations/acquisition-runs/recover-expired'): ('OPERATIONS_RECOVERY',
                                                                  'Recover expired acquisition runs'),
 ('POST', '/admin/operations/outbox-events/{event_id}/retry'): ('OPERATIONS_RECOVERY',
                                                                'Retry dead outbox event'),
 ('POST', '/admin/operations/work-items/{work_item_id}/cancel'): ('OPERATIONS_RECOVERY', 'Cancel work item'),
 ('POST', '/admin/operations/work-items/{work_item_id}/replay'): ('OPERATIONS_RECOVERY', 'Replay work item'),
 ('POST', '/admin/operations/work-items/{work_item_id}/retry'): ('OPERATIONS_RECOVERY', 'Retry work item'),
 ('POST', '/admin/smtp/configurations'): ('CONFIGURATION', 'SMTP draft'),
 ('POST', '/admin/smtp/configurations/{config_id}/publish'): ('CONFIGURATION',
                                                              'SMTP publish (test-before-publish)'),
 ('POST', '/admin/smtp/configurations/{config_id}/rollback'): ('CONFIGURATION', 'SMTP rollback as new draft'),
 ('POST', '/admin/smtp/configurations/{config_id}/test'): ('INTEGRATION', 'SMTP connectivity test'),
 ('POST', '/admin/sources'): ('SOURCE_OPERATIONS', 'Register source'),
 ('POST', '/admin/sources/{source_id}/acquisition-runs'): ('SOURCE_OPERATIONS', 'Run acquisition'),
 ('POST', '/admin/sources/{source_id}/connectors/{connector_id}/configuration-versions/{version_number}/activate'): ('SOURCE_OPERATIONS',
                                                                                                                     'Activate '
                                                                                                                     'connector '
                                                                                                                     'config '
                                                                                                                     'version'),
 ('POST', '/admin/sources/{source_id}/connectors/{connector_id}/configuration-versions/{version_number}/publish'): ('SOURCE_OPERATIONS',
                                                                                                                    'Publish '
                                                                                                                    'connector '
                                                                                                                    'config '
                                                                                                                    'version'),
 ('POST', '/admin/sources/{source_id}/connectors/{connector_id}/health-check'): ('SOURCE_OPERATIONS',
                                                                                 'Connector health check'),
 ('POST', '/admin/support/tickets/{ticket_id}/messages'): ('INTERVENTION',
                                                           'Administrator reply (attributed to the admin)'),
 ('POST', '/admin/target-policy/forbidden-rules'): ('DISCOVERY_POLICY', 'Create forbidden rule')}

INTERVENTION_CATALOGUE = {
    ("POST", "/admin/notifications/email-deliveries/{delivery_id}/retry"),
    ("PATCH", "/admin/support/tickets/{ticket_id}/assignment"),
    ("POST", "/admin/support/tickets/{ticket_id}/messages"),
    ("PATCH", "/admin/support/tickets/{ticket_id}/status"),
    ("PATCH", "/admin/users/{user_id}/status"),
}


def _routes():
    found = set()
    for router in (admin_api.router, support_api.admin_router):
        for route in router.routes:
            for method in (getattr(route, "methods", None) or set()) & MUTATING:
                path = route.path if route.path.startswith(router.prefix) else router.prefix + route.path
                found.add((method, path))
    return found


def test_every_mutating_admin_route_is_classified_and_none_is_stale():
    assert _routes() == set(INVENTORY), (_routes() ^ set(INVENTORY))


def test_intervention_catalogue_is_exactly_the_decided_allowlist():
    classified = {key for key, (category, _note) in INVENTORY.items() if category == "INTERVENTION"}
    assert classified == INTERVENTION_CATALOGUE


def test_no_admin_route_can_submit_or_mutate_an_application():
    for method, path in _routes() | {("GET", "/admin/impersonation-sessions/{session_id}/workspace")}:
        assert "application" not in path.replace("acquisition-runs", "").lower(), (method, path)
    assert not any(
        m in {"POST", "PUT", "PATCH", "DELETE"} and "workspace" in p for m, p in _routes()
    )


def test_inventory_document_lists_every_route():
    from pathlib import Path
    text = Path("docs/admin/ADMIN_ACTION_INVENTORY.md").read_text()
    for method, path in INVENTORY:
        assert f"`{method} /api/v1{path}`" in text, (method, path)
