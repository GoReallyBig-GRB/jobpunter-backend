from app.infrastructure.acquisition.registry import AcquisitionConnectorRegistry


def test_url_target_capabilities_are_explicit_and_fail_closed():
    registry = AcquisitionConnectorRegistry()

    assert registry.supports_target("SCRAPY", "1.0.0", "CRAWL")
    assert registry.supports_target("SCRAPY", "1.0.0", "SEARCH")
    assert registry.supports_target("PLAYWRIGHT", "1.0.0", "CRAWL")
    assert registry.supports_target("PLAYWRIGHT", "1.0.0", "SEARCH")
    assert registry.supports_target("JSON_API", "1.0.0", "SEARCH")
    assert registry.supports_target("RSS_ATOM", "1.0.0", "SEARCH")

    assert not registry.supports_target("JOBSPY", "1.0.0", "CRAWL")
    assert not registry.supports_target("JOBSPY", "1.0.0", "SEARCH")
    # Greenhouse accepts only the canonical board-token jobs URL as a SEARCH target;
    # the mission worker rejects any other URL (see discovery_mission_worker).
    assert registry.supports_target("GREENHOUSE_JOB_BOARD", "1", "SEARCH")
    assert not registry.supports_target("GREENHOUSE_JOB_BOARD", "1", "CRAWL")
    assert not registry.supports_target("UNKNOWN", "1", "CRAWL")
    assert not registry.supports_target("SCRAPY", "unsupported-version", "CRAWL")
    assert not registry.supports_target("SCRAPY", "1.0.0", "UNKNOWN")
