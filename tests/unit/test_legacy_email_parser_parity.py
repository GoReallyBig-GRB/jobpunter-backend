from app.application.jobs.email_parser import (
    detect_email_source,
    extract_single_job_from_text,
    has_multiple_headings,
    is_job_alert_email,
    parse_job_alert_email,
)


def test_email_source_and_alert_detection_cover_known_job_platforms():
    assert detect_email_source("Job alert", "", "alerts@indeed.com") == "Indeed"
    assert detect_email_source("", "Find roles on Wellfound", "alerts@example.com") == "Wellfound"
    assert detect_email_source("", "", "jobs@weworkremotely.com") == "We Work Remotely"
    assert is_job_alert_email("Your new jobs for you", "")
    assert not is_job_alert_email("Monthly newsletter", "Company news and updates")


def test_single_job_extraction_uses_explicit_fields_and_preserves_raw_description():
    text = (
        "Title: Product Manager\n"
        "Company: Acme\n"
        "Location: Remote - Nigeria\n"
        "Apply: https://example.com/jobs/1\n"
        "\nRequirements and responsibilities go here."
    )
    parsed = extract_single_job_from_text(text, "Indeed")
    assert parsed["title"] == "Product Manager"
    assert parsed["company"] == "Acme"
    assert parsed["location"] == "Remote - Nigeria"
    assert parsed["jobUrl"] == "https://example.com/jobs/1"
    assert parsed["description"] == text
    assert parsed["source"] == "Indeed"


def test_email_parser_marks_unstructured_single_job_for_manual_review():
    result = parse_job_alert_email("Job alert", "Some unstructured content", "alerts@example.com")
    assert result["isMultiJobAlert"] is False
    assert result["parsingStatus"] == "PARSING_REQUIRED"
    assert result["sourceEmail"]["processedStatus"] == "PARSING_REQUIRED"
    assert result["extractedJobs"] == []
    assert result["reviewRequiredCount"] == 1
    assert result["reviewRequiredChunks"][0]["content"]


def test_email_parser_splits_multi_job_digest_with_explicit_delimiters():
    content = (
        "### Product Manager\nTitle: Product Manager\nCompany: Acme\n"
        "https://acme.example/jobs/1\n" + ("Responsibilities include managing product delivery, coordinating stakeholders, defining requirements, and improving workflows. Qualifications require product management experience, communication skills, and technical fluency. " * 2)
        + "\n---\n### Product Owner\nTitle: Product Owner\nCompany: Beta\n"
        "https://beta.example/jobs/2\n" + ("Responsibilities include owning technical roadmaps, coordinating engineering, and improving platform reliability. Qualifications include technical product ownership experience, communication skills, and clear stakeholder communication. " * 2)
    )
    result = parse_job_alert_email("2 new jobs", content, "alerts@linkedin.com")
    assert result["isMultiJobAlert"] is True
    assert result["parsingStatus"] == "PROCESSED"
    assert len(result["extractedJobs"]) == 2
    assert all(job["sourceEmailId"] == result["sourceEmail"]["id"] for job in result["extractedJobs"])


def test_email_parser_does_not_guess_when_multi_job_digest_cannot_be_split():
    result = parse_job_alert_email("18 new jobs", "A digest with multiple jobs but no usable delimiters.")
    assert result["isMultiJobAlert"] is True
    assert result["parsingStatus"] == "PARSING_REQUIRED"
    assert result["extractedJobs"] == []
    assert result["sourceEmail"]["extractedJobCount"] == 0


def test_heading_detection_counts_multiple_role_markers():
    assert has_multiple_headings("Role: PM\nPosition: PO")
    assert not has_multiple_headings("Role: PM only")
