"""Firebase Authentication ID-token verification and local identity mapping."""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
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

    @staticmethod
    def _claim_text(claims: Mapping[str, Any], key: str, max_length: int) -> str | None:
        value = claims.get(key)
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value or len(value) > max_length:
            return None
        return value

    @classmethod
    def _account_claims(cls, claims: Mapping[str, Any]) -> dict[str, Any]:
        display_name = cls._claim_text(claims, "name", 200)
        email = cls._claim_text(claims, "email", 320)
        if email is not None:
            email = email.casefold()
            if "@" not in email or any(character.isspace() for character in email):
                email = None

        photo_url = cls._claim_text(claims, "picture", 2048)
        if photo_url is not None and not photo_url.startswith("https://"):
            photo_url = None

        updates: dict[str, Any] = {}
        if display_name is not None:
            updates["display_name"] = display_name
        if email is not None:
            updates["email"] = email
        if isinstance(claims.get("email_verified"), bool):
            updates["email_verified"] = claims["email_verified"]
        if photo_url is not None:
            updates["photo_url"] = photo_url

        auth_time = claims.get("auth_time")
        if isinstance(auth_time, (int, float)) and not isinstance(auth_time, bool) and auth_time > 0:
            try:
                updates["last_login_at"] = datetime.fromtimestamp(auth_time, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                pass
        return updates

    @classmethod
    def _sync_account_claims(cls, user: User, claims: Mapping[str, Any]) -> bool:
        """Update only valid, present claims; never erase stored values for missing claims."""
        changed = False
        updates = cls._account_claims(claims)
        last_login_at = updates.pop("last_login_at", None)
        for key, value in updates.items():
            if getattr(user, key, None) != value:
                setattr(user, key, value)
                changed = True

        if last_login_at is not None:
            current_last_login = getattr(user, "last_login_at", None)
            if current_last_login is None:
                user.last_login_at = last_login_at
                changed = True
            else:
                if current_last_login.tzinfo is None:
                    current_last_login = current_last_login.replace(tzinfo=timezone.utc)
                if last_login_at > current_last_login.astimezone(timezone.utc):
                    user.last_login_at = last_login_at
                    changed = True
        return changed

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
        if not isinstance(claims, Mapping):
            raise ValueError("Firebase token claims are invalid")
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
            if self._sync_account_claims(user, claims):
                db.commit()
            return AuthenticatedPrincipal(
                user_id=user.id,
                identity_id=identity.id,
                auth_provider="firebase",
            )

        account_claims = self._account_claims(claims)
        user = User(
            status="ACTIVE",
            onboarding_status="NOT_STARTED",
            **account_claims,
        )
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
            if self._sync_account_claims(user, claims):
                db.commit()
            return AuthenticatedPrincipal(
                user_id=user.id,
                identity_id=identity.id,
                auth_provider="firebase",
            )
