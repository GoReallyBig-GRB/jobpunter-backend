from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class AuthorizationContext:
    user_id: UUID
    is_admin: bool = False


def can_access_user_resource(context: AuthorizationContext, resource_user_id: UUID) -> bool:
    return context.is_admin or context.user_id == resource_user_id
