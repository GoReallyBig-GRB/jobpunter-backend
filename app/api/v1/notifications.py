from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.notifications.service import NotificationService
from app.infrastructure.db.models import NotificationEmailPreference, User
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate/notifications", tags=["candidate notifications"])


def _payload(row) -> dict:
    return {
        "id": str(row.id),
        "event_type": row.event_type,
        "title": row.title,
        "message": row.message,
        "resource_type": row.resource_type,
        "resource_id": str(row.resource_id) if row.resource_id else None,
        "created_at": row.created_at.isoformat(),
        "read_at": row.read_at.isoformat() if row.read_at else None,
        "is_read": row.read_at is not None,
    }


@router.get("")
def list_notifications(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    unread_only: bool = Query(default=False),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    rows, total = NotificationService(db).list_for_user(
        principal.user_id, limit=limit, offset=offset, unread_only=unread_only
    )
    return {
        "data": [_payload(row) for row in rows],
        "pagination": {"limit": limit, "offset": offset, "total": total},
    }


@router.post("/{notification_id}/read")
def mark_notification_read(
    notification_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    row = NotificationService(db).mark_read(principal.user_id, notification_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"data": _payload(row)}


class EmailPreferenceRequest(BaseModel):
    enabled: bool


@router.get("/email-preferences")
def get_email_preferences(
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    preference = db.get(NotificationEmailPreference, principal.user_id)
    return {"data": {"enabled": bool(preference and preference.enabled)}}


@router.put("/email-preferences")
def update_email_preferences(
    request: EmailPreferenceRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, principal.user_id)
    if request.enabled and (user is None or not user.email or not user.email_verified):
        raise HTTPException(
            status_code=409,
            detail="A verified email address is required to enable email notifications",
        )
    preference = db.get(NotificationEmailPreference, principal.user_id)
    if preference is None:
        preference = NotificationEmailPreference(
            user_id=principal.user_id,
            enabled=request.enabled,
            updated_at=datetime.now(timezone.utc),
        )
        db.add(preference)
    else:
        preference.enabled = request.enabled
        preference.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"data": {"enabled": preference.enabled}}
