from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    user_id: UUID
    identity_id: UUID | None = None


class IdentityProvider:
    def authenticate(self, authorization_header: str | None) -> AuthenticatedPrincipal:
        raise NotImplementedError
