import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.api.errors import DomainError
from app.application.documents.imports import DocumentImportService
from app.application.documents.structured_conflicts import (
    canonical_field_name,
    find_structured_conflicts,
)


def _evidence(content, *, status="APPROVED", evidence_type="profile"):
    return SimpleNamespace(
        id="evidence-1",
        status=status,
        evidence_type=evidence_type,
        content=content,
    )


def test_canonical_field_names_accept_common_aliases():
    assert canonical_field_name("email_address") == "email"
    assert canonical_field_name("Candidate Name") == "full_name"
    assert canonical_field_name("phone-number") == "phone"
    assert canonical_field_name("job_title") is None


def test_structured_conflict_detects_different_approved_candidate_email():
    conflicts = find_structured_conflicts(
        {"fields": [{"name": "email address", "value": "new@example.com"}]},
        [_evidence({"contact": {"email": "old@example.com"}})],
    )

    assert len(conflicts) == 1
    assert conflicts[0]["code"] == "POSSIBLE_FIELD_CONFLICT"
    assert conflicts[0]["field"] == "email"
    assert conflicts[0]["imported_value"] == "new@example.com"
    assert conflicts[0]["existing_value"] == "old@example.com"


def test_structured_conflict_normalizes_equivalent_values():
    conflicts = find_structured_conflicts(
        {"fields": [{"name": "phone_number", "value": "+1 (555) 010-2000"}]},
        [_evidence({"phone": "15550102000"})],
    )

    assert conflicts == []


def test_structured_conflicts_ignore_unapproved_evidence():
    conflicts = find_structured_conflicts(
        {"fields": [{"name": "full_name", "value": "A. Candidate"}]},
        [_evidence({"name": "Another Candidate"}, status="DRAFT")],
    )

    assert conflicts == []


def test_structured_conflicts_do_not_compare_multi_valued_employment_facts():
    conflicts = find_structured_conflicts(
        {"fields": [{"name": "role", "value": "Product Manager"}]},
        [_evidence({"role": "Project Manager"}, evidence_type="employment_record")],
    )

    assert conflicts == []


class _FakeDB:
    def __init__(self):
        self.commits = 0
        self.refreshed = []

    def commit(self):
        self.commits += 1

    def refresh(self, record):
        self.refreshed.append(record)

    def scalar(self, _statement):
        return self.current_record


def test_structured_extraction_persists_server_validated_fields(monkeypatch):
    db = _FakeDB()
    service = DocumentImportService(db)
    profile = SimpleNamespace(user_id="user-1")
    record = SimpleNamespace(
        id="import-1",
        status="PARSED",
        extracted_text="Name: Alex Candidate\\nEmail: alex@example.com",
        extracted_fields={},
        conflicts=[],
        revision=1,
        updated_at=None,
    )
    db.current_record = record
    service._owned_import = lambda _user_id, _import_id: (profile, record, object())

    async def fake_execute(request, *, db, user_id):
        assert request.task == "document_parsing"
        assert "Alex Candidate" in request.prompt
        assert user_id == "user-1"
        return SimpleNamespace(execution_id="test-execution-1", parsed_data={
            "document_type": "resume",
            "fields": [{
                "name": "full_name",
                "value": "Alex Candidate",
                "source_text": "Name: Alex Candidate",
                "confidence": "high",
            }],
            "conflicts": [],
            "unresolved_items": [],
        })

    monkeypatch.setattr("app.application.documents.imports.execute_ai_task", fake_execute)
    result = asyncio.run(service.extract_fields("user-1", "import-1"))

    assert result is record
    assert record.extracted_fields["fields"][0]["value"] == "Alex Candidate"
    assert record.status == "PREVIEWED"
    assert record.revision == 2
    assert record.updated_at is not None
    assert db.commits == 1


def test_structured_extraction_rejects_oversized_prompt_without_truncation(monkeypatch):
    db = _FakeDB()
    service = DocumentImportService(db)
    profile = SimpleNamespace(user_id="user-1")
    record = SimpleNamespace(
        status="PARSED",
        extracted_text="x" * 70_001,
        extracted_fields={},
        conflicts=[],
        revision=1,
        updated_at=None,
    )
    service._owned_import = lambda _user_id, _import_id: (profile, record, object())

    async def should_not_execute(*_args, **_kwargs):
        raise AssertionError("AI must not receive silently truncated document text")

    monkeypatch.setattr("app.application.documents.imports.execute_ai_task", should_not_execute)
    with pytest.raises(DomainError) as error:
        asyncio.run(service.extract_fields("user-1", "import-1"))

    assert error.value.code == "DOCUMENT_TEXT_TOO_LONG_FOR_EXTRACTION"
    assert error.value.status_code == 413
    assert db.commits == 0


def test_structured_conflicts_ignore_expired_approved_evidence():
    evidence = _evidence({"email": "old@example.com"})
    evidence.expires_at = datetime.now(timezone.utc) - timedelta(days=1)

    conflicts = find_structured_conflicts(
        {"fields": [{"name": "email", "value": "new@example.com"}]},
        [evidence],
    )

    assert conflicts == []
