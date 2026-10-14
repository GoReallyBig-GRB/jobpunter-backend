"""Trusted identity-provider contract and authenticated principal types."""
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    user_id: UUID
    identity_id: UUID | None = None
    auth_provider: str = "development"


class IdentityProvider(Protocol):
    """Provider adapter contract used by the API authentication boundary."""

    def authenticate(
        self,
        authorization_header: str | None,
        db: Session,
    ) -> AuthenticatedPrincipal:
        """Verify credentials and map them to an active internal user."""
        ...
