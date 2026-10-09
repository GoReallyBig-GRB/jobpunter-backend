from datetime import datetime, timezone

from app.application.jobs.normalization import evaluate_freshness


def test_freshness_scores_closed_recent_current_and_stale_jobs_deterministically():
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    assert evaluate_freshness(None, "This job is closed", now=now)["status"] == "CLOSED_EXPIRED"
    assert evaluate_freshness("2026-10-08", now=now) == {
        "status": "RECENTLY_POSTED",
        "postingDate": "2026-10-08",
        "daysSincePosted": 1,
        "score": 10,
    }
    assert evaluate_freshness("2026-09-20", now=now)["status"] == "CURRENT_ACTIVE"
    stale = evaluate_freshness("2026-08-01", now=now)
    assert stale["status"] == "STALE"
    assert stale["score"] == 3
    assert stale["daysSincePosted"] == 69


def test_freshness_marks_missing_or_invalid_dates_uncertain():
    now = datetime(2026, 10, 9, tzinfo=timezone.utc)
    assert evaluate_freshness(now=now)["status"] == "AGE_UNCERTAIN"
    invalid = evaluate_freshness("not a date", now=now)
    assert invalid["status"] == "AGE_UNCERTAIN"
    assert invalid["warning"] == "Unparseable date format."


def test_freshness_handles_timezone_aware_iso_dates():
    result = evaluate_freshness(
        "2026-10-08T12:00:00+00:00",
        now=datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc),
    )
    assert result["status"] == "RECENTLY_POSTED"
    assert result["daysSincePosted"] == 1
