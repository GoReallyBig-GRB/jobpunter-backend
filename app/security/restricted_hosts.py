"""Job platforms JobPunter's own automation must not access without recorded written permission.

Indeed: based on terms summarised by the user. LinkedIn, Glassdoor, ZipRecruiter: conservative
assumption, NOT verified against their current terms (see docs/operations/acquisition-live-canaries.md).

User-initiated routes (a user pasting a URL, description or email) are separate and unaffected.
Lifting a restriction is a business decision: record the platform's written permission reference
(JobSpy: ``context.site_permissions``) or remove the entry here deliberately.
"""
from __future__ import annotations

# JobSpy site name -> registrable domain served by that platform.
RESTRICTED_SITE_DOMAINS: dict[str, str] = {
    "indeed": "indeed.com",
    "linkedin": "linkedin.com",
    "glassdoor": "glassdoor.com",
    "zip_recruiter": "ziprecruiter.com",
}
_RESTRICTED_DOMAINS = frozenset(RESTRICTED_SITE_DOMAINS.values())


def is_restricted_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    host = hostname.strip().lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in _RESTRICTED_DOMAINS)


def permission_reference(context: dict, site: str) -> str | None:
    """The recorded written-permission reference for ``site``, or None."""
    permissions = context.get("site_permissions")
    if not isinstance(permissions, dict):
        return None
    value = permissions.get(site)
    if isinstance(value, str) and value.strip():
        return value.strip()[:200]
    return None
