from app.application.jobs.normalization import evaluate_location_and_work_auth


def test_location_and_work_auth_hard_rejects_missing_required_authorization():
    result = evaluate_location_and_work_auth(
        "Remote, US",
        "US work authorization required",
        {"eligibleCountries": ["Nigeria"], "workAuthorizations": [], "remotePreference": "REMOTE_OK"},
    )
    assert result["eligibility"] == "REGION_RESTRICTED"
    assert result["hardReject"] is True
    assert result["score"] == 0


def test_location_and_work_auth_matches_country_and_worldwide_remote():
    nigeria = evaluate_location_and_work_auth(
        "Remote",
        "Candidates based in Nigeria are welcome",
        {"eligibleCountries": ["Nigeria"], "workAuthorizations": [], "remotePreference": "REMOTE_OK"},
    )
    assert nigeria["eligibility"] == "NIGERIA_OK"
    assert nigeria["score"] == 20
    assert nigeria["hardReject"] is False

    worldwide = evaluate_location_and_work_auth(
        "Remote",
        "Fully remote, open to international candidates worldwide",
        {"eligibleCountries": ["Nigeria"], "workAuthorizations": [], "remotePreference": "REMOTE_OK"},
    )
    assert worldwide["eligibility"] == "WORLDWIDE_REMOTE"
    assert worldwide["score"] == 18


def test_location_and_work_auth_marks_generic_remote_unclear_and_foreign_onsite_hard_no_go():
    remote = evaluate_location_and_work_auth(
        "Remote",
        "Remote role",
        {"eligibleCountries": ["Nigeria"], "workAuthorizations": [], "remotePreference": "REMOTE_ONLY"},
    )
    assert remote["eligibility"] == "UNCLEAR"
    assert remote["score"] == 10
    assert remote["hardReject"] is False

    onsite = evaluate_location_and_work_auth(
        "London",
        "Hybrid, must work in London",
        {"eligibleCountries": ["Nigeria"], "workAuthorizations": [], "remotePreference": "REMOTE_ONLY", "willingToRelocate": False},
    )
    assert onsite["eligibility"] == "HYBRID_ONSITE"
    assert onsite["hardReject"] is True
    assert onsite["score"] == 0
