from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.db.models import (
    Application,
    ApplicationEvent,
    CandidateProfile,
    Job,
    Notification,
    Punt,
)


def _insert_notification(db: Session, values: dict) -> None:
    """Insert once per persisted source event; duplicate worker delivery is harmless."""
    db.execute(
        pg_insert(Notification)
        .values(**values)
        .on_conflict_do_nothing(constraint="uq_notifications_event_key")
    )
    db.commit()


def punt_notification_handler(session_factory: sessionmaker):
    """Create an in-app alert from a persisted, newly qualified Punt."""
    def handle(payload: dict) -> None:
        try:
            punt_id = UUID(str(payload["punt_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid_punt_notification_payload") from exc

        with session_factory() as db:
            row = db.execute(
                select(Punt, CandidateProfile, Job)
                .join(CandidateProfile, CandidateProfile.id == Punt.candidate_profile_id)
                .join(Job, Job.id == Punt.job_id)
                .where(Punt.id == punt_id)
            ).first()
            if row is None:
                raise ValueError("persisted_punt_not_found")
            punt, profile, job = row
            title = (job.canonical_title or "this role").strip()
            company = (job.company_name or "").strip()
            role = f"{title} at {company}" if company else title
            _insert_notification(db, {
                "user_id": profile.user_id,
                "event_key": f"punt-created:{punt.id}",
                "event_type": "PUNT_CREATED",
                "title": "New Punt available",
                "message": f"A new Punt is available for {role}.",
                "resource_type": "PUNT",
                "resource_id": punt.id,
                "created_at": punt.qualified_at,
                "read_at": None,
            })
    return handle


def application_status_notification_handler(session_factory: sessionmaker):
    """Create an in-app alert from a persisted application status transition."""
    def handle(payload: dict) -> None:
        try:
            event_id = UUID(str(payload["application_event_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid_application_notification_payload") from exc

        with session_factory() as db:
            row = db.execute(
                select(ApplicationEvent, Application, CandidateProfile, Job)
                .join(Application, Application.id == ApplicationEvent.application_id)
                .join(CandidateProfile, CandidateProfile.id == Application.candidate_profile_id)
                .join(Job, Job.id == Application.job_id)
                .where(ApplicationEvent.id == event_id)
            ).first()
            if row is None:
                raise ValueError("persisted_application_event_not_found")
            event, application, profile, job = row
            if event.event_type != "APPLICATION_STATUS_CHANGED":
                # Application creation is recorded, but the PRD's initial alert is for
                # tracked status changes rather than every lifecycle event.
                return
            title = (job.canonical_title or "this role").strip()
            company = (job.company_name or "").strip()
            role = f"{title} at {company}" if company else title
            _insert_notification(db, {
                "user_id": profile.user_id,
                "event_key": f"application-status:{event.id}",
                "event_type": "APPLICATION_STATUS_CHANGED",
                "title": "Application status updated",
                "message": f"Your application for {role} changed to {event.to_status}.",
                "resource_type": "APPLICATION",
                "resource_id": application.id,
                "created_at": event.occurred_at,
                "read_at": None,
            })
    return handle


class NotificationService:
    """Candidate-scoped notification reads and read-state mutation."""

    def __init__(self, db: Session):
        self.db = db

    def list_for_user(
        self, user_id: UUID, *, limit: int = 20, offset: int = 0, unread_only: bool = False
    ) -> tuple[list[Notification], int]:
        filters = [Notification.user_id == user_id]
        if unread_only:
            filters.append(Notification.read_at.is_(None))
        total = self.db.scalar(
            select(func.count()).select_from(Notification).where(*filters)
        ) or 0
        rows = list(self.db.scalars(
            select(Notification)
            .where(*filters)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset(offset)
            .limit(limit)
        ).all())
        return rows, int(total)

    def mark_read(self, user_id: UUID, notification_id: UUID) -> Notification | None:
        row = self.db.scalar(
            select(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
            .with_for_update()
        )
        if row is None:
            return None
        if row.read_at is None:
            row.read_at = datetime.now(timezone.utc)
            self.db.commit()
            self.db.refresh(row)
        return row
