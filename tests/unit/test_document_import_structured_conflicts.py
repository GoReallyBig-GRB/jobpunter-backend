from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.application.documents.structured_conflicts import find_structured_conflicts


def evidence(content, *, status="APPROVED", expires_at=None, evidence_id="evidence-1"):
    return SimpleNamespace(
        id=evidence_id,
        status=status,
        evidence_type="identity_record",
        content=content,
        effective_from=None,
        effective_to=None,
        expires_at=expires_at,
    )


def test_import_conflict_detection_flags_two_distinct_values_for_one_identity_field():
    conflicts = find_structured_conflicts({
        "fields": [
            {"name": "email", "value": "first@example.com", "source_text": "Email: first@example.com"},
            {"name": "contact_email", "value": "second@example.com", "source_text": "Email: second@example.com"},
        ],
    }, [])

    assert len(conflicts) == 1
    assert conflicts[0]["code"] == "CONFLICTING_IMPORTED_VALUES"
    assert conflicts[0]["field"] == "email"
    assert set(conflicts[0]["imported_values"]) == {"first@example.com", "second@example.com"}


def test_import_conflict_detection_compares_with_saved_profile_identity():
    conflicts = find_structured_conflicts({
        "fields": [{"name": "full_name", "value": "New Candidate Name", "source_text": "Name: New Candidate Name"}],
    }, [], profile_data={"identity": {"full_name": "Old Candidate Name"}})

    assert len(conflicts) == 1
    assert conflicts[0]["code"] == "POSSIBLE_PROFILE_FIELD_CONFLICT"
    assert conflicts[0]["field"] == "full_name"
    assert conflicts[0]["existing_value"] == "Old Candidate Name"


def test_import_conflict_detection_compares_with_current_approved_evidence():
    conflicts = find_structured_conflicts({
        "fields": [{"name": "email_address", "value": "new@example.com"}],
    }, [evidence({"contact_email": "old@example.com"})])

    assert len(conflicts) == 1
    assert conflicts[0]["code"] == "POSSIBLE_FIELD_CONFLICT"
    assert conflicts[0]["field"] == "email"
    assert conflicts[0]["existing_value"] == "old@example.com"


def test_import_conflict_detection_ignores_expired_evidence():
    conflicts = find_structured_conflicts({
        "fields": [{"name": "phone", "value": "+234 800 111 2222"}],
    }, [evidence(
        {"mobile_number": "+234 800 999 0000"},
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )])

    assert conflicts == []


def test_matching_imported_profile_and_evidence_values_are_not_conflicts():
    conflicts = find_structured_conflicts({
        "fields": [{"name": "email", "value": "candidate@example.com"}],
    }, [evidence({"email_address": "candidate@example.com"})],
       profile_data={"identity": {"email": "candidate@example.com"}})

    assert conflicts == []
