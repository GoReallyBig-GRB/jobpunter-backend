import pytest

from app.application.jobs.target_policy import (
    ForbiddenTargetError,
    assert_target_not_forbidden,
    matching_forbidden_rule,
    normalize_forbidden_rule,
)


def rule(rule_type, pattern, *, scope="ALL", active=True, rule_id="rule-1"):
    return {
        "id": rule_id,
        "rule_type": rule_type,
        "pattern": normalize_forbidden_rule(rule_type, pattern),
        "connector_scope": scope,
        "reason": "Test policy",
        "active": active,
    }


def test_host_exact_blocks_only_the_exact_normalized_host():
    rules = [rule("HOST_EXACT", "EXAMPLE.COM.")]
    assert matching_forbidden_rule("https://example.com/jobs", rules, connector_type="SCRAPY")
    assert matching_forbidden_rule("https://sub.example.com/jobs", rules, connector_type="SCRAPY") is None


def test_host_suffix_matches_subdomains_without_suffix_confusion():
    rules = [rule("HOST_SUFFIX", "*.example.com")]
    assert matching_forbidden_rule("https://example.com/jobs", rules, connector_type="PLAYWRIGHT")
    assert matching_forbidden_rule("https://jobs.example.com/jobs", rules, connector_type="PLAYWRIGHT")
    assert matching_forbidden_rule("https://notexample.com/jobs", rules, connector_type="PLAYWRIGHT") is None


def test_exact_url_preserves_query_parameters_and_ignores_fragments():
    rules = [rule("URL_EXACT", "HTTPS://EXAMPLE.COM:443/jobs?id=42#details")]
    assert matching_forbidden_rule(
        "https://example.com/jobs?id=42", rules, connector_type="JSON_API"
    )
    assert matching_forbidden_rule(
        "https://example.com/jobs?id=43", rules, connector_type="JSON_API"
    ) is None


def test_path_prefix_matches_path_segments_but_not_similar_prefixes():
    rules = [rule("URL_PATH_PREFIX", "https://example.com/jobs")]
    assert matching_forbidden_rule(
        "https://example.com/jobs/123", rules, connector_type="RSS_ATOM"
    )
    assert matching_forbidden_rule(
        "https://example.com/jobs", rules, connector_type="RSS_ATOM"
    )
    assert matching_forbidden_rule(
        "https://example.com/jobs-archive", rules, connector_type="RSS_ATOM"
    ) is None


def test_connector_scope_and_inactive_rules_are_respected():
    rules = [
        rule("HOST_EXACT", "blocked.example", scope="SCRAPY", rule_id="1"),
        rule("HOST_EXACT", "inactive.example", active=False, rule_id="2"),
    ]
    assert matching_forbidden_rule(
        "https://blocked.example/jobs", rules, connector_type="SCRAPY"
    )
    assert matching_forbidden_rule(
        "https://blocked.example/jobs", rules, connector_type="PLAYWRIGHT"
    ) is None
    assert matching_forbidden_rule(
        "https://inactive.example/jobs", rules, connector_type="SCRAPY"
    ) is None


@pytest.mark.parametrize(
    ("rule_type", "pattern"),
    [
        ("HOST_EXACT", "https://example.com/path"),
        ("URL_EXACT", "https://user:password@example.com/jobs"),
        ("URL_EXACT", "https://example.com/jobs?access_token=secret"),
        ("URL_PATH_PREFIX", "https://example.com/jobs?page=2"),
        ("UNKNOWN", "example.com"),
    ],
)
def test_invalid_forbidden_rule_patterns_are_rejected(rule_type, pattern):
    with pytest.raises(ValueError):
        normalize_forbidden_rule(rule_type, pattern)


def test_assertion_raises_a_safe_error_for_a_matching_rule():
    rules = [rule("HOST_SUFFIX", "example.com", rule_id="rule-42")]
    with pytest.raises(ForbiddenTargetError) as exc_info:
        assert_target_not_forbidden(
            "https://jobs.example.com/posting", rules, connector_type="SCRAPY"
        )
    assert exc_info.value.rule_id == "rule-42"
    assert "https://" not in str(exc_info.value)
