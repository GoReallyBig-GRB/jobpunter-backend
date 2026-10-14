import pytest

from app.domain.candidate.claim_support import (
    claim_fact_conflicts_with_evidence,
    claim_facts_are_supported,
)


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



def test_same_field_disagreement_is_reported_as_a_conflict():
    conflicts = claim_fact_conflicts_with_evidence(
        {"years_experience": 5},
        [{"years": 7}],
    )

    assert conflicts == [{
        "field": "years",
        "claim_value": "5",
        "evidence_values": "7",
    }]


def test_matching_fact_is_not_reported_as_a_conflict():
    assert claim_fact_conflicts_with_evidence(
        {"employer": "Example Co"},
        [{"company": "Example Co"}],
    ) == []


def test_unrelated_fields_are_missing_support_not_conflicts():
    assert claim_fact_conflicts_with_evidence(
        {"years_experience": 5},
        [{"employer": "Example Co"}],
    ) == []
    assert not claim_facts_are_supported(
        {"years_experience": 5},
        [{"employer": "Example Co"}],
    )


def test_claim_matching_one_of_several_evidence_values_still_surfaces_conflict():
    conflicts = claim_fact_conflicts_with_evidence(
        {"years_experience": 5},
        [{"years": 5}, {"years": 7}],
    )

    assert len(conflicts) == 1
    assert conflicts[0]["evidence_values"] == "7"



def test_claim_conflicts_compare_equivalent_fields_across_matching_nested_records():
    conflicts = claim_fact_conflicts_with_evidence(
        {
            "career_history": [{
                "employer": "Example Ltd",
                "role": "Product Manager",
                "start_date": "2020",
            }],
        },
        [{
            "work_experience": [{
                "company": "Example Ltd",
                "job_title": "Product Manager",
                "employment_start_date": "2021",
            }],
        }],
    )

    assert any(
        conflict["field"] == "start_date"
        and conflict["claim_value"] == "2020"
        and conflict["evidence_values"] == "2021"
        for conflict in conflicts
    )


def test_claim_conflicts_do_not_join_different_employment_records_by_role_alone():
    conflicts = claim_fact_conflicts_with_evidence(
        {
            "career_history": [{
                "employer": "First Company",
                "role": "Product Manager",
                "start_date": "2020",
            }],
        },
        [{
            "work_experience": [{
                "company": "Second Company",
                "job_title": "Product Manager",
                "employment_start_date": "2021",
            }],
        }],
    )

    assert not any(conflict["field"] == "start_date" for conflict in conflicts)
