from enum import StrEnum

class JobIdentityStatus(StrEnum):
    CANONICAL = "CANONICAL"
    SUPERSEDED = "SUPERSEDED"
    MERGED = "MERGED"

class JobLifecycleStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"

class JobFreshnessStatus(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
