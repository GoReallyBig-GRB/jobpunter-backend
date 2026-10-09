"""Firebase Authentication ID-token verification and local identity mapping."""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.infrastructure.db.models.identity import Identity, User
from app.ports.identity import AuthenticatedPrincipal


class FirebaseIdentityProvider:
    """Verify Firebase ID tokens and map Firebase UIDs to durable application users."""

    def __init__(self, token_verifier: Callable[[str], dict[str, Any]] | None = None):
        self._token_verifier = token_verifier

    @staticmethod
    def _verify_with_firebase(token: str) -> dict[str, Any]:
        import firebase_admin
        from firebase_admin import auth

        project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
        if not project_id:
            raise RuntimeError("FIREBASE_PROJECT_ID is required for Firebase authentication")

        try:
            app = firebase_admin.get_app()
        except ValueError:
            try:
                app = firebase_admin.initialize_app(options={"projectId": project_id})
            except Exception as exc:
                raise RuntimeError("Firebase Admin credentials or configuration are unavailable") from exc

        # Never verify tokens against a pre-existing default app for another Firebase project.
        if getattr(app, "project_id", None) != project_id:
            raise RuntimeError("Firebase Admin app project does not match FIREBASE_PROJECT_ID")

        try:
            return auth.verify_id_token(token, app=app, check_revoked=True)
        except Exception as exc:
            # Token failures are authentication failures; no decoded claims are trusted.
            raise ValueError("Firebase ID token is invalid, expired, or revoked") from exc

    def authenticate(
        self,
        authorization_header: str | None,
        db: Session,
    ) -> AuthenticatedPrincipal:
        if not authorization_header:
            raise ValueError("Bearer token required")
        scheme, separator, token = authorization_header.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            raise ValueError("Bearer token required")

        verifier = self._token_verifier or self._verify_with_firebase
        claims = verifier(token.strip())
        firebase_uid = claims.get("uid")
        if not isinstance(firebase_uid, str) or not firebase_uid.strip():
            raise ValueError("Firebase token does not contain a UID")

        identity = db.scalar(
            select(Identity).where(
                Identity.provider == "FIREBASE",
                Identity.provider_subject == firebase_uid,
            )
        )
        if identity is not None:
            user = db.get(User, identity.user_id)
            if user is None or user.status != "ACTIVE":
                raise ValueError("Application user is not active")
            return AuthenticatedPrincipal(
                user_id=user.id,
                identity_id=identity.id,
                auth_provider="firebase",
            )

        user = User(display_name=claims.get("name"), status="ACTIVE")
        db.add(user)
        try:
            db.flush()
            identity = Identity(
                user_id=user.id,
                provider="FIREBASE",
                provider_subject=firebase_uid,
            )
            db.add(identity)
            db.commit()
            db.refresh(identity)
            return AuthenticatedPrincipal(
                user_id=user.id,
                identity_id=identity.id,
                auth_provider="firebase",
            )
        except IntegrityError:
            # Concurrent first sign-ins may race on the provider/subject unique key.
            db.rollback()
            identity = db.scalar(
                select(Identity).where(
                    Identity.provider == "FIREBASE",
                    Identity.provider_subject == firebase_uid,
                )
            )
            if identity is None:
                raise
            user = db.get(User, identity.user_id)
            if user is None or user.status != "ACTIVE":
                raise ValueError("Application user is not active")
            return AuthenticatedPrincipal(
                user_id=user.id,
                identity_id=identity.id,
                auth_provider="firebase",
            )
