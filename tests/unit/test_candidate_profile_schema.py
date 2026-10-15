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

    assert patch.achievements[0].outcome is None
    assert patch.achievements[0].verification_status == "UNVERIFIED"


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



def test_profile_patch_rejects_wrong_types_for_known_career_fields():
    with pytest.raises(ValidationError):
        CandidateProfileSectionsPatch.model_validate({
            "career_history": [{"role": 42, "employer": "Example Co"}],
        })


def test_profile_patch_preserves_forward_compatible_profile_fields():
    patch = CandidateProfileSectionsPatch.model_validate({
        "projects": [{
            "name": "Workflow rebuild",
            "outcome": None,
            "verification_status": "UNVERIFIED",
            "custom_product_context": {"users": ["SMEs"]},
        }],
    })

    record = patch.model_dump(exclude_unset=True)["projects"][0]
    assert record["name"] == "Workflow rebuild"
    assert record["verification_status"] == "UNVERIFIED"
    assert record["custom_product_context"] == {"users": ["SMEs"]}


def test_profile_patch_rejects_record_with_only_unset_or_blank_known_fields():
    with pytest.raises(ValidationError):
        CandidateProfileSectionsPatch.model_validate({
            "education": [{"institution": "   ", "qualification": None}],
        })



@pytest.mark.parametrize(
    ("section", "payload"),
    [
        ("identity", {"email": 123}),
        ("eligibility", {"eligible_countries": "Nigeria"}),
        ("professional_identity", {"years_experience": "many"}),
    ],
)
def test_profile_patch_type_checks_identity_and_eligibility_fields(section, payload):
    with pytest.raises(ValidationError):
        CandidateProfileSectionsPatch.model_validate({section: payload})



@pytest.mark.parametrize(
    ("section", "payload"),
    [
        ("career_history", {"employer": 7}),
        ("projects", {"product_status": ["shipped"]}),
        ("achievements", {"achievement": 3}),
        ("leadership", {"team_size": "many"}),
        ("skills_tools", {"years_experience": "several"}),
        ("education", {"institution": 9}),
        ("certifications", {"credential_url": 17}),
    ],
)
def test_profile_sections_apply_section_specific_field_types(section, payload):
    with pytest.raises(ValidationError):
        CandidateProfileSectionsPatch.model_validate({section: [payload]})


def test_profile_project_schema_accepts_delivery_state_and_preserves_unknown_fields():
    patch = CandidateProfileSectionsPatch.model_validate({
        "projects": [{
            "name": "Workflow rebuild",
            "description": "Rebuilt a workflow",
            "product_status": "prototype",
            "custom_delivery_context": {"environment": "staging"},
        }],
    })

    record = patch.projects[0].model_dump(exclude_unset=True)
    assert record["product_status"] == "prototype"
    assert record["custom_delivery_context"] == {"environment": "staging"}


def test_profile_sections_response_validates_existing_nested_records():
    from app.application.candidates.schemas import CandidateProfileSections

    profile = CandidateProfileSections.model_validate({
        "career_history": [{"role": "Analyst", "employer": "Example Co"}],
        "projects": [{"name": "Internal tool", "product_status": "shipped"}],
    })

    assert profile.career_history[0]["employer"] == "Example Co"
    assert profile.projects[0]["product_status"] == "shipped"

def test_prd_achievement_context_action_source_and_unverified_outcome_are_explicit():
    patch = CandidateProfileSectionsPatch.model_validate({
        "achievements": [{
            "achievement": "Improved support workflow",
            "context": "Support requests were handled manually",
            "action": "Introduced a triage workflow",
            "outcome": None,
            "metrics": None,
            "source": {"kind": "candidate_statement"},
            "verification_status": "UNVERIFIED",
        }],
    })

    record = patch.achievements[0].model_dump(exclude_unset=True)
    assert record["context"] == "Support requests were handled manually"
    assert record["action"] == "Introduced a triage workflow"
    assert record["outcome"] is None
    assert record["metrics"] is None
    assert record["source"] == {"kind": "candidate_statement"}
    assert record["verification_status"] == "UNVERIFIED"


def test_prd_achievement_context_action_and_source_enforce_declared_types():
    with pytest.raises(ValidationError):
        CandidateProfileSectionsPatch.model_validate({
            "achievements": [{
                "achievement": "Improved support workflow",
                "context": 7,
            }],
        })

    with pytest.raises(ValidationError):
        CandidateProfileSectionsPatch.model_validate({
            "achievements": [{
                "achievement": "Improved support workflow",
                "action": ["introduced", "triage"],
            }],
        })

