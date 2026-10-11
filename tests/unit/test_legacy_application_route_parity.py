from app.application.jobs.normalization import evaluate_application_route


def test_application_route_hard_rejects_explicit_paid_gates_at_zero_fee():
    result = evaluate_application_route(
        "https://example.com/job",
        "",
        "A paid subscription required to apply.",
        {"maxApplicationFee": 0},
    )
    assert result["status"] == "HARD_REJECT"
    assert result["score"] == 0
    assert result["verificationStatus"] == "Blocked ($0 application policy violation)"


def test_application_route_accepts_known_free_ats_and_reputable_boards():
    ats = evaluate_application_route("https://boards.greenhouse.io/acme/jobs/123", "", "", {})
    assert ats["status"] == "GOOD"
    assert ats["score"] == 10
    assert "greenhouse.io" in ats["evidence"]

    board = evaluate_application_route("https://www.linkedin.com/jobs/view/123", "", "", {})
    assert board["status"] == "GOOD"
    assert board["score"] == 8


def test_application_route_handles_careers_missing_and_unknown_destinations():
    assert evaluate_application_route("https://acme.com/careers", "", "", {})["score"] == 9
    assert evaluate_application_route("", "", "", {})["score"] == 2
    unknown = evaluate_application_route("https://redirect.example/r?id=2", "", "", {})
    assert unknown["status"] == "REVIEW"
    assert unknown["score"] == 5


def test_application_route_respects_nonzero_fee_preference():
    result = evaluate_application_route(
        "https://example.com/job",
        "",
        "Application fee required.",
        {"maxApplicationFee": 20},
    )
    assert result["status"] == "REVIEW"
