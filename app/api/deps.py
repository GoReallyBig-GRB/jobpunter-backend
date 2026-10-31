"""Authentication dependencies and provider-agnostic identity adapter registry."""
from collections.abc import Callable
import os
from threading import RLock
from uuid import UUID

from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session

from app.infrastructure.db.session import get_db
from app.infrastructure.identity.firebase import FirebaseIdentityProvider
from app.ports.identity import AuthenticatedPrincipal, IdentityProvider


IdentityProviderFactory = Callable[[], IdentityProvider]
_identity_provider_factories: dict[str, IdentityProviderFactory] = {
    # Resolve at call time so the adapter binding is not frozen at import time.
    "firebase": lambda: FirebaseIdentityProvider(),
}
_identity_provider_registry_lock = RLock()


def register_identity_provider(name: str, factory: IdentityProviderFactory) -> None:
    """Register an adapter factory without coupling application identity to its vendor.

    Registration is intended for application startup/composition and tests. Provider
    subjects must be mapped by the adapter to the durable internal PostgreSQL user ID;
    provider email addresses are not account-linking keys.
    """
    normalized = name.strip().lower()
    if not normalized or not normalized.replace("-", "").replace("_", "").isalnum():
        raise ValueError("Identity provider name must be a nonblank identifier")
    if not callable(factory):
        raise TypeError("Identity provider factory must be callable")
    with _identity_provider_registry_lock:
        _identity_provider_factories[normalized] = factory


def get_identity_provider() -> IdentityProvider:
    """Resolve the configured adapter; Firebase is the startup default."""
    provider_name = os.getenv("JOBPUNTER_IDENTITY_PROVIDER", "firebase").strip().lower()
    with _identity_provider_registry_lock:
        factory = _identity_provider_factories.get(provider_name)
    if factory is None:
        # Unknown providers fail closed; never silently fall back to Firebase or dev identity.
        raise RuntimeError("Configured identity provider is unsupported")
    return factory()


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
