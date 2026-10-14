"""Authentication dependencies for development and configured identity providers."""
import os
from uuid import UUID

from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session

from app.infrastructure.db.session import get_db
from app.infrastructure.identity.firebase import FirebaseIdentityProvider
from app.ports.identity import AuthenticatedPrincipal, IdentityProvider


def get_identity_provider() -> IdentityProvider:
    """Resolve the configured identity provider at the trusted backend boundary."""
    provider_name = os.getenv("JOBPUNTER_IDENTITY_PROVIDER", "firebase").strip().lower()
    if provider_name == "firebase":
        return FirebaseIdentityProvider()
    # Unknown providers fail closed; never silently fall back to Firebase or dev identity.
    raise RuntimeError("Configured identity provider is unsupported")


def get_principal(
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> AuthenticatedPrincipal:
    """Use the configured verifier by default; allow spoofable IDs only in opt-in dev."""
    environment = os.getenv("JOBPUNTER_ENVIRONMENT", "").strip().lower()
    allow_dev_identity = os.getenv("JOBPUNTER_ALLOW_DEV_IDENTITY", "").strip().lower() == "true"
    if environment == "development" and allow_dev_identity:
        try:
            user_id = UUID(x_user_id) if x_user_id else None
        except ValueError:
            user_id = None
        if user_id is not None:
            return AuthenticatedPrincipal(user_id=user_id, auth_provider="development")

    try:
        return get_identity_provider().authenticate(authorization, db)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid or missing authentication token") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Authentication service is not configured") from exc
