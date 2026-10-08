from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class AuthorizationContext:
    user_id: UUID
    roles: frozenset[str] = frozenset({"USER"})

    def is_admin(self) -> bool:
        return "ADMIN" in self.roles


def require_owner(context: AuthorizationContext, resource_user_id: UUID) -> None:
    if context.user_id != resource_user_id:
        raise PermissionError("Resource is not owned by the authenticated user")


def require_admin(context: AuthorizationContext) -> None:
    if not context.is_admin():
        raise PermissionError("Administrative access required")
