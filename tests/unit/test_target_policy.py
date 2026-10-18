import pytest

from app.infrastructure.acquisition.json_api import JsonApiConnector, JsonApiConnectorError
from app.infrastructure.acquisition.rss_atom import RssAtomConnector, FeedConnectorError
from app.infrastructure.acquisition.playwright import PlaywrightConnector
from app.infrastructure.acquisition.scrapy import ScrapyConnector
from app.infrastructure.acquisition.jobspy import JobSpyConnector
from app.infrastructure.connectors.greenhouse import GreenhouseJobBoardConnector, GreenhouseConnectorError
from app.ports.acquisition import AcquisitionAdapterError, AcquisitionOutcome

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


def test_path_prefix_is_scoped_to_the_normalized_origin():
    https_rule = [rule("URL_PATH_PREFIX", "https://example.com/jobs")]
    http_rule = [rule("URL_PATH_PREFIX", "http://example.com/jobs")]

    assert matching_forbidden_rule(
        "https://example.com/jobs/123", https_rule, connector_type="SCRAPY"
    )
    assert matching_forbidden_rule(
        "http://example.com/jobs/123", https_rule, connector_type="SCRAPY"
    ) is None
    assert matching_forbidden_rule(
        "http://example.com/jobs/123", http_rule, connector_type="SCRAPY"
    )
    assert matching_forbidden_rule(
        "https://example.com/jobs/123", http_rule, connector_type="SCRAPY"
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

class NoNetworkClient:
    def __init__(self):
        self.calls = 0

    def stream(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("blocked targets must be rejected before HTTP")


def test_json_api_forbidden_target_is_rejected_before_http():
    client = NoNetworkClient()
    connector = JsonApiConnector(http_client=client, allow_unsafe_http_client=True)
    rules = [rule("HOST_EXACT", "example.com")]
    with pytest.raises(JsonApiConnectorError) as exc_info:
        connector.acquire(
            source_id=__import__("uuid").uuid4(),
            context={
                "endpoint_url": "https://example.com/jobs",
                "forbidden_target_rules": rules,
            },
        )
    assert exc_info.value.error_category == "forbidden_target_rule_matched"
    assert exc_info.value.status == AcquisitionOutcome.RESTRICTED
    assert client.calls == 0


def test_rss_forbidden_target_is_rejected_before_http():
    client = NoNetworkClient()
    connector = RssAtomConnector(http_client=client, allow_unsafe_http_client=True)
    rules = [rule("HOST_EXACT", "feeds.example.com")]
    with pytest.raises(FeedConnectorError) as exc_info:
        connector.acquire(
            source_id=__import__("uuid").uuid4(),
            context={
                "feed_url": "https://feeds.example.com/jobs.xml",
                "forbidden_target_rules": rules,
            },
        )
    assert exc_info.value.error_category == "forbidden_target_rule_matched"
    assert exc_info.value.status == AcquisitionOutcome.RESTRICTED
    assert client.calls == 0


@pytest.mark.parametrize(
    ("connector", "context"),
    [
        (
            PlaywrightConnector(),
            {"start_urls": ["https://blocked.example/jobs"]},
        ),
        (
            ScrapyConnector(),
            {"start_urls": ["https://blocked.example/jobs"]},
        ),
        (
            JobSpyConnector(),
            {"forbidden_target_rules": [rule("HOST_SUFFIX", "example.com")]},
        ),
    ],
)
def test_connector_policy_denials_happen_before_connector_network_execution(monkeypatch, connector, context):
    context["forbidden_target_rules"] = [rule("HOST_EXACT", "blocked.example")] if "start_urls" in context else context["forbidden_target_rules"]
    if isinstance(connector, ScrapyConnector):
        monkeypatch.setattr(
            "app.infrastructure.acquisition.scrapy.subprocess.run",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("worker must not start")),
        )
    with pytest.raises((AcquisitionAdapterError, ValueError)) as exc_info:
        connector.acquire(source_id=__import__("uuid").uuid4(), context=context)
    assert "forbidden" in str(exc_info.value).lower()

def test_greenhouse_health_check_denies_policy_target_before_http():
    client = NoNetworkClient()
    connector = GreenhouseJobBoardConnector(http_client=client, allow_unsafe_http_client=True)
    with pytest.raises(GreenhouseConnectorError) as exc_info:
        connector.health_check(context={
            "board_token": "example-board",
            "forbidden_target_rules": [rule("HOST_EXACT", "boards-api.greenhouse.io")],
        })
    assert exc_info.value.error_category == "FORBIDDEN_TARGET_RULE_MATCHED"
    assert exc_info.value.status == AcquisitionOutcome.RESTRICTED
    assert client.calls == 0

