import ipaddress
import pathlib

import pytest

from app.security.network_safety import is_public_unicast


@pytest.mark.parametrize("address", [
    "8.8.8.8", "1.1.1.1", "93.184.216.34", "2001:4860:4860::8888", "2606:4700:4700::1111",
])
def test_ordinary_public_unicast_addresses_are_allowed(address):
    assert is_public_unicast(ipaddress.ip_address(address)) is True


@pytest.mark.parametrize("address", [
    "224.0.0.1", "224.0.1.1", "233.252.0.1", "239.255.255.250", "ff02::1", "ff0e::1", "ff05::2",
    "::ffff:224.0.0.1",
])
def test_multicast_is_rejected_even_though_is_global_accepts_it(address):
    parsed = ipaddress.ip_address(address)
    assert parsed.is_global  # the stdlib gap this helper closes
    assert is_public_unicast(parsed) is False


@pytest.mark.parametrize("address", [
    "10.0.0.1", "172.16.0.1", "192.168.1.1", "127.0.0.1", "169.254.169.254", "0.0.0.0",
    "100.64.0.1", "::1", "fe80::1", "fc00::1", "::",
])
def test_private_loopback_link_local_and_unspecified_remain_rejected(address):
    assert is_public_unicast(ipaddress.ip_address(address)) is False


def test_no_module_decides_public_reachability_with_is_global_directly():
    """Every reachability check must go through the shared helper (regression guard)."""
    root = pathlib.Path(__file__).resolve().parents[2] / "app"
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if path.name != "network_safety.py" and ".is_global" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


MULTICAST_URLS = ["http://224.0.0.1/jobs", "https://239.255.255.250/jobs", "https://[ff02::1]/jobs", "https://[ff0e::1]/jobs"]


@pytest.mark.parametrize("url", MULTICAST_URLS)
def test_manual_url_validation_rejects_multicast_literals(url):
    from app.application.jobs.manual_url_retrieval import JobUrlRetrievalError, _validate_public_url

    with pytest.raises(JobUrlRetrievalError) as raised:
        _validate_public_url(url)
    assert "NON_PUBLIC_ADDRESS" in str(raised.value) or getattr(raised.value, "code", "") == "NON_PUBLIC_ADDRESS"


@pytest.mark.parametrize("url", MULTICAST_URLS)
def test_target_policy_and_source_normalization_reject_multicast_literals(url):
    from app.application.jobs.source_governance import normalize_public_target_url
    from app.application.jobs.target_policy import classify_target_policy_error, _normalized_request_url

    assert classify_target_policy_error(url) == "NON_PUBLIC_ADDRESS"
    with pytest.raises(ValueError):
        normalize_public_target_url(url)
    with pytest.raises(ValueError):
        _normalized_request_url(url)


@pytest.mark.parametrize("address", ["224.0.0.1", "ff02::1"])
def test_scrapy_dns_guard_rejects_multicast_answers(address):
    from app.infrastructure.acquisition.scrapy_worker_runtime import _require_public_resolved_address

    with pytest.raises(ValueError):
        _require_public_resolved_address(address)
    assert _require_public_resolved_address("8.8.8.8") == "8.8.8.8"
