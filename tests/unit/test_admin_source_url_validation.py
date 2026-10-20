import pytest
from fastapi import HTTPException

from app.api.v1.admin import _validate_connector_configuration


def test_source_registry_normalizes_safe_start_urls_without_dropping_query_parameters():
    configuration = {
        "start_urls": ["HTTPS://8.8.8.8:443/jobs?market=CA#section"],
    }

    _validate_connector_configuration("SCRAPY", configuration)

    assert configuration["start_urls"] == ["https://8.8.8.8/jobs?market=CA"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://[::1]/admin",
        "https://user:password@8.8.8.8/jobs",
        "https://localhost/jobs",
        "https://8.8.8.8/jobs?access_token=secret",
        "file:///etc/passwd",
        "https://8.8.8.8:8443/jobs",
    ],
)
def test_source_registry_rejects_unsafe_start_urls(url):
    with pytest.raises(HTTPException) as exc_info:
        _validate_connector_configuration("PLAYWRIGHT", {"start_urls": [url]})

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "UNSAFE_SOURCE_TARGET_URL"


def test_json_api_and_rss_registry_urls_require_https_and_are_normalized():
    json_configuration = {"endpoint_url": "HTTPS://8.8.8.8:443/jobs?region=ng#listing"}
    rss_configuration = {"feed_url": "https://8.8.8.8:443/feed.xml"}

    _validate_connector_configuration("JSON_API", json_configuration)
    _validate_connector_configuration("RSS_ATOM", rss_configuration)

    assert json_configuration["endpoint_url"] == "https://8.8.8.8/jobs?region=ng"
    assert rss_configuration["feed_url"] == "https://8.8.8.8/feed.xml"


def test_json_api_and_rss_registry_reject_non_https_and_embedded_credentials():
    for connector_type, field, value in (
        ("JSON_API", "endpoint_url", "http://8.8.8.8/jobs"),
        ("RSS_ATOM", "feed_url", "https://user:password@8.8.8.8/feed"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _validate_connector_configuration(connector_type, {field: value})
        assert exc_info.value.status_code == 422
        assert exc_info.value.detail["code"] == "UNSAFE_SOURCE_TARGET_URL"
