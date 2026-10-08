from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class AuthenticatedIdentity:
    provider: str
    provider_subject: str
    user_id: UUID


class IdentityProvider(Protocol):
    def authenticate(self, credential: str) -> AuthenticatedIdentity: ...
