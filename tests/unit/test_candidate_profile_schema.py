import pytest
from pydantic import ValidationError

from app.application.candidates.schemas import CandidateProfileSectionsPatch, CandidateProfileUpdate


@pytest.mark.parametrize(
    ("section", "record"),
    [
        ("career_history", {}),
        ("projects", {"name": "  ", "outcome": None}),
        ("achievements", {"context": "", "evidence": []}),
    ],
)
def test_profile_patch_rejects_empty_repeatable_records(section, record):
    with pytest.raises(ValidationError):
        CandidateProfileSectionsPatch.model_validate({section: [record]})


def test_profile_patch_allows_clearing_a_repeatable_section():
    patch = CandidateProfileSectionsPatch.model_validate({"career_history": []})

    assert patch.career_history == []


def test_profile_patch_allows_explicit_unverified_fact_without_inventing_outcome():
    patch = CandidateProfileSectionsPatch.model_validate({
        "achievements": [{
            "achievement": "Reduced support backlog",
            "outcome": None,
            "verification_status": "UNVERIFIED",
        }]
    })

    assert patch.achievements[0]["outcome"] is None
    assert patch.achievements[0]["verification_status"] == "UNVERIFIED"


def test_profile_patch_accepts_selected_structured_sections():
    payload = CandidateProfileUpdate.model_validate({
        "expected_revision": 1,
        "sections": {
            "identity": {"full_name": "Candidate"},
            "career_history": [{"role": "Analyst", "employer": "Example Co"}],
        },
    })

    assert payload.sections.model_dump(exclude_unset=True) == {
        "identity": {"full_name": "Candidate"},
        "career_history": [{"role": "Analyst", "employer": "Example Co"}],
    }


def test_profile_patch_rejects_unknown_section_names():
    with pytest.raises(ValidationError):
        CandidateProfileSectionsPatch.model_validate({"work_preferences": {"remote": True}})


def test_profile_patch_rejects_null_instead_of_silently_clearing_a_section():
    with pytest.raises(ValidationError, match="Profile sections cannot be null"):
        CandidateProfileSectionsPatch.model_validate({"identity": None})


def test_profile_patch_requires_at_least_one_section():
    with pytest.raises(ValidationError, match="At least one profile section"):
        CandidateProfileSectionsPatch.model_validate({})
