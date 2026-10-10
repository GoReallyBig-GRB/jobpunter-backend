import pytest

from app.domain.candidate.claim_support import claim_facts_are_supported


def test_claim_support_matches_known_employment_field_aliases():
    assert claim_facts_are_supported(
        {"employer": "Example Ltd", "role": "Product Manager", "years": 4},
        [{"organization": " example ltd ", "title": "product manager", "years_experience": "4"}],
    )


@pytest.mark.parametrize(
    ("claim", "evidence"),
    [
        ({"employer": "Example Ltd", "years": 4}, [{"organization": "Example Ltd"}]),
        ({"employer": "Example Ltd", "years": 4}, [{"organization": "Example Ltd", "years": 3}]),
        ({"employer": "Example Ltd"}, [{"employer": "Other Ltd"}]),
        ({}, [{"employer": "Example Ltd"}]),
    ],
)
def test_claim_support_fails_closed_for_missing_conflicting_or_empty_facts(claim, evidence):
    assert not claim_facts_are_supported(claim, evidence)


def test_claim_support_can_aggregate_facts_across_multiple_evidence_items():
    assert claim_facts_are_supported(
        {"employer": "Example Ltd", "role": "Product Manager"},
        [
            {"organization": "Example Ltd"},
            {"position": "Product Manager"},
        ],
    )
