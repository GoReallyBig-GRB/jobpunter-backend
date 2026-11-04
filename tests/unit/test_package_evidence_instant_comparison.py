from app.application.application_packages.service import _comparable_evidence_value


def test_same_instant_in_different_utc_offsets_compares_equal():
    utc = "2026-10-28T22:09:24.162028+00:00"
    lagos = "2026-10-28T23:09:24.162028+01:00"
    assert _comparable_evidence_value("effective_to", utc) == _comparable_evidence_value(
        "effective_to", lagos
    )


def test_different_instants_still_compare_unequal():
    assert _comparable_evidence_value(
        "expires_at", "2026-10-28T22:09:24+00:00"
    ) != _comparable_evidence_value("expires_at", "2026-10-28T22:09:25+00:00")


def test_non_instant_fields_are_compared_unchanged():
    assert _comparable_evidence_value("status", "APPROVED") == "APPROVED"
    assert _comparable_evidence_value("effective_from", None) is None
