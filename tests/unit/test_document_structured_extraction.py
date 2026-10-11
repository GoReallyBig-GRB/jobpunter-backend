from types import SimpleNamespace

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
