"""Deterministic source governance checks for automated acquisition.

The source registry stores permission review metadata in the source's governed
configuration until a dedicated registry API/schema is introduced.
"""

from collections.abc import Mapping


PERMISSION_REVIEW_STATUS_KEY = "permission_review_status"
APPROVED_PERMISSION_REVIEW_STATUS = "APPROVED"


def assert_automated_acquisition_permitted(source: object) -> None:
    """Fail closed unless a source has an explicitly approved terms/permission review."""
    configuration = getattr(source, "configuration", None)
    if not isinstance(configuration, Mapping):
        raise ValueError("source permission review is not approved for automated acquisition")

    status = configuration.get(PERMISSION_REVIEW_STATUS_KEY)
    if not isinstance(status, str) or status.strip().upper() != APPROVED_PERMISSION_REVIEW_STATUS:
        raise ValueError("source permission review is not approved for automated acquisition")
