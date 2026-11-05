import importlib
from unittest.mock import patch


def test_permission_evidence_migration_generates_audit_event_ids():
    migration = importlib.import_module("migrations.versions.0068_source_permission_evidence")

    with patch.object(migration.op, "execute") as execute:
        migration.upgrade()

    audit_insert = str(execute.call_args_list[0].args[0])
    assert "id, event_type" in audit_insert
    assert "gen_random_uuid()" in audit_insert
