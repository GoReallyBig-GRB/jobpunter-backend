"""Every mutating Admin handler must reach an audit writer, or be explicitly justified.

This is a static guard: it fails when a new mutating Admin/support-admin route is added
without any audit reference. Behavioral success/failure audit evidence lives in the
PostgreSQL tests named in each justification.
"""
import inspect
import re

from app.api.v1 import admin as admin_api
from app.api.v1 import support as support_api

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
AUDIT_TOKEN = re.compile(r"[A-Za-z_]*[Aa]udit[A-Za-z_]*|_record_work_item_action")

# Handlers that do not name an audit helper themselves, with where the audit is written.
DELEGATED = {
    "preview_decision_rules": "read-only preview; intentionally no mutation audit",
    "resolve_job_identity_resolution": (
        "audited by the identity-resolution service and centralized failure handling "
        "(tests/integration/test_job_resolution_admin_postgres.py)"
    ),
    "admin_add_message": "support service writes AuditEvent SUPPORT_MESSAGE_SENT",
    "admin_change_status": "support service writes AuditEvent SUPPORT_TICKET_STATUS_CHANGED",
    "admin_assign_ticket": "support service writes AuditEvent SUPPORT_TICKET_ASSIGNED",
}


def _mutating_handlers():
    handlers = {}
    for router in (admin_api.router, support_api.admin_router):
        for route in router.routes:
            if (getattr(route, "methods", None) or set()) & MUTATING:
                handlers[route.endpoint.__name__] = route.endpoint
    return handlers


def test_every_mutating_admin_handler_reaches_an_audit_writer_or_is_justified():
    handlers = _mutating_handlers()
    assert len(handlers) >= 30
    unaudited = [
        name for name, endpoint in handlers.items()
        if not AUDIT_TOKEN.search(inspect.getsource(endpoint)) and name not in DELEGATED
    ]
    assert not unaudited, f"Mutating Admin handlers with no audit reference: {unaudited}"


def test_delegated_allowlist_has_no_stale_or_secretly_audited_entries():
    handlers = _mutating_handlers()
    assert set(DELEGATED) <= set(handlers), set(DELEGATED) - set(handlers)
    # An entry that now names an audit helper directly no longer needs the exception.
    redundant = [
        name for name in DELEGATED
        if AUDIT_TOKEN.search(inspect.getsource(handlers[name]))
    ]
    assert not redundant, f"Remove redundant allowlist entries: {redundant}"
