import pytest
from pydantic import ValidationError

from app.application.candidates.schemas import CandidateProfileSectionsPatch, CandidateProfileUpdate


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
