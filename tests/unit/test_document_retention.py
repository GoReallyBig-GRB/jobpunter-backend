from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.application.documents.retention import is_retention_eligible


NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(days=30)


def _document(**overrides):
    values = {
        "application_package_id": None,
        "application_package_item_id": None,
        "metadata_json": {},
        "created_at": NOW - timedelta(days=31),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _import(**overrides):
    values = {"status": "FAILED", "saved_evidence_id": None}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_retention_allows_only_old_terminal_unreferenced_imports():
    assert is_retention_eligible(_document(), _import(), CUTOFF)
    assert not is_retention_eligible(_document(), _import(status="UPLOADED"), CUTOFF)
    assert not is_retention_eligible(_document(), _import(status="SAVED", saved_evidence_id="e1"), CUTOFF)
    assert not is_retention_eligible(_document(application_package_id="p1"), _import(), CUTOFF)
    assert not is_retention_eligible(_document(created_at=NOW - timedelta(days=2)), _import(), CUTOFF)
    assert not is_retention_eligible(_document(), None, CUTOFF)


def test_retention_does_not_expire_a_document_twice():
    document = _document(metadata_json={"retention": {"status": "EXPIRED"}})
    assert not is_retention_eligible(document, _import(), CUTOFF)
