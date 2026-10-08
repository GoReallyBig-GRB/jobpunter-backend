from fastapi import Header, HTTPException
from uuid import UUID

from app.ports.identity import AuthenticatedPrincipal


def get_principal(x_user_id: str | None = Header(default=None)) -> AuthenticatedPrincipal:
    """Development-only identity seam. Production authentication will replace this adapter."""
    try:
        user_id = UUID(x_user_id) if x_user_id else None
    except ValueError:
        user_id = None
    if user_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return AuthenticatedPrincipal(user_id=user_id)
