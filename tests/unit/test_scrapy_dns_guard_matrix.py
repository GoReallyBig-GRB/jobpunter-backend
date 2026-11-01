"""L1.5: Scrapy's connection-time DNS guard rejects every non-public address class, including redirect targets."""
import pytest

pytest.importorskip("scrapy")

from twisted.internet import defer  # noqa: E402

from app.infrastructure.acquisition import scrapy_worker_runtime as runtime  # noqa: E402

NON_PUBLIC = [
    "127.0.0.1", "127.255.255.254", "::1", "0.0.0.0", "10.0.0.5", "172.16.0.1", "172.31.255.255",
    "192.168.1.1", "169.254.169.254", "100.64.0.1", "fc00::1", "fd12:3456::1", "fe80::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "255.255.255.255",
]
# Known, documented limitation (proposal, not changed here): every acquisition guard classifies with
# ipaddress.is_global, which accepts multicast ranges such as 224.0.0.1. A TCP HTTP connection cannot be
# established to a multicast address, so this is not an SSRF path; tightening it would touch ~14 call sites.
PUBLIC = ["93.184.216.34", "8.8.8.8", "2606:2800:220:1:248:1893:25c8:1946"]


@pytest.mark.parametrize("address", NON_PUBLIC)
def test_non_public_resolved_address_is_rejected(address):
    with pytest.raises(ValueError):
        runtime._require_public_resolved_address(address)


@pytest.mark.parametrize("address", PUBLIC)
def test_public_resolved_address_is_accepted(address):
    assert runtime._require_public_resolved_address(address)


@pytest.mark.parametrize("garbage", ["", "not-an-ip", "999.1.1.1", "127.0.0.1/8"])
def test_unparseable_resolution_answer_is_rejected(garbage):
    with pytest.raises(ValueError):
        runtime._require_public_resolved_address(garbage)


@pytest.mark.parametrize("address", NON_PUBLIC)
def test_resolver_wrapper_fails_the_lookup_instead_of_returning_a_private_address(address):
    class InnerResolver:
        def getHostByName(self, name, timeout=()):
            return defer.succeed(address)

    resolver = runtime.PublicOnlyCachingThreadedResolver.__new__(runtime.PublicOnlyCachingThreadedResolver)
    resolver._resolver = InnerResolver()
    outcome = []
    resolver.getHostByName("redirect-target.example").addCallbacks(
        lambda value: outcome.append(("ok", value)), lambda failure: outcome.append(("err", failure.value))
    )
    assert outcome and outcome[0][0] == "err" and isinstance(outcome[0][1], ValueError)
