from app.domain.candidate.provenance import source_excerpt_is_verifiable


def test_source_excerpt_verification_accepts_case_and_whitespace_variants():
    assert source_excerpt_is_verifiable(
        "Name: Example Candidate\nTechnical Product Manager",
        " name:   example candidate ",
    )


def test_source_excerpt_verification_rejects_absent_or_empty_excerpts():
    assert not source_excerpt_is_verifiable(
        "Name: Example Candidate",
        "Name: Different Candidate",
    )
    assert not source_excerpt_is_verifiable("some document text", "   ")
    assert not source_excerpt_is_verifiable(None, "some excerpt")
