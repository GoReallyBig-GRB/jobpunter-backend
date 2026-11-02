"""Single definition of "safe to connect to" for outbound destinations.

`ipaddress.is_global` alone accepts multicast ranges, so every reachability check goes through
this helper instead of testing `is_global` directly.
"""
import ipaddress


def is_public_unicast(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for globally routable unicast addresses; multicast is never a valid fetch target."""
    return bool(address.is_global) and not address.is_multicast
