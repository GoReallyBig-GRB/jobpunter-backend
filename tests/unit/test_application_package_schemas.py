import pytest
from pydantic import ValidationError

from app.application.application_packages.schemas import ApplicationPackageCreate


def test_package_accepts_cv_cover_letter_and_editable_q_and_a():
    request = ApplicationPackageCreate.model_validate({
        "items": [
            {"artifact_type": "tailored_cv", "content": {"content": "Tailored CV text"}},
            {"artifact_type": "cover_letter", "content": {"content": "Cover letter text"}},
            {
                "artifact_type": "application_q_and_a",
                "content": {"answers": [{
                    "question": "Why this role?",
                    "answer": "My editable draft answer.",
                    "evidence_basis": ["evidence UUID"],
                    "confidence": "medium",
                    "unsupported_claims": [],
                    "missing_information": [],
                }]},
            },
        ],
    })
    assert {item.artifact_type for item in request.items} == {
        "tailored_cv", "cover_letter", "application_q_and_a"
    }


@pytest.mark.parametrize(
    "item",
    [
        {"artifact_type": "tailored_cv", "content": {"content": "  "}},
        {"artifact_type": "cover_letter", "content": {"text": "Wrong key"}},
        {"artifact_type": "application_q_and_a", "content": {"answers": [{"question": "Why?"}]}},
        {"artifact_type": "application_q_and_a", "content": {"answers": [{
            "question": "Why?", "answer": "Draft", "confidence": "certain"
        }]}},
    ],
)
def test_package_rejects_incomplete_or_invalid_artifact(item):
    with pytest.raises(ValidationError):
        ApplicationPackageCreate.model_validate({"items": [item]})


def test_package_rejects_duplicate_artifact_types_in_one_version():
    with pytest.raises(ValidationError):
        ApplicationPackageCreate.model_validate({"items": [
            {"artifact_type": "cover_letter", "content": {"content": "First"}},
            {"artifact_type": "cover_letter", "content": {"content": "Second"}},
        ]})


def test_package_request_cannot_smuggle_unrecognized_fields():
    with pytest.raises(ValidationError):
        ApplicationPackageCreate.model_validate({"items": [{
            "artifact_type": "cover_letter",
            "content": {"content": "Draft"},
            "candidate_profile_id": "caller-controlled-owner",
        }]})
