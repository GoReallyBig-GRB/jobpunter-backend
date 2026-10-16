from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.db.models import (
    Application,
    ApplicationEvent,
    CandidateEvaluation,
    CandidateProfile,
    DiscoveryMission,
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



def evaluation_attention_notification_handler(session_factory: sessionmaker):
    """Create an in-app alert from a persisted evaluation that requires candidate review."""
    def handle(payload: dict) -> None:
        try:
            evaluation_id = UUID(str(payload["evaluation_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid_evaluation_notification_payload") from exc

        with session_factory() as db:
            row = db.execute(
                select(CandidateEvaluation, CandidateProfile, Job)
                .join(CandidateProfile, CandidateProfile.id == CandidateEvaluation.candidate_profile_id)
                .join(Job, Job.id == CandidateEvaluation.job_id)
                .where(CandidateEvaluation.id == evaluation_id)
            ).first()
            if row is None:
                raise ValueError("persisted_evaluation_not_found")
            evaluation, profile, job = row
            if evaluation.outcome != "REVIEW":
                return
            title = (job.canonical_title or "this role").strip()
            company = (job.company_name or "").strip()
            role = f"{title} at {company}" if company else title
            _insert_notification(db, {
                "user_id": profile.user_id,
                "event_key": f"evaluation-attention:{evaluation.id}",
                "event_type": "EVALUATION_REQUIRES_ATTENTION",
                "title": "Evaluation needs your review",
                "message": f"Your evaluation for {role} needs your review before you decide how to proceed.",
                "resource_type": "EVALUATION",
                "resource_id": evaluation.id,
                "created_at": evaluation.evaluated_at,
                "read_at": None,
            })
    return handle


def discovery_completion_notification_handler(session_factory: sessionmaker):
    """Create an in-app alert from a persisted terminal discovery mission."""
    terminal_statuses = {"TARGET_MET", "EXHAUSTED", "FAILED", "CANCELLED"}

    def handle(payload: dict) -> None:
        try:
            mission_id = UUID(str(payload["mission_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid_discovery_notification_payload") from exc

        with session_factory() as db:
            row = db.execute(
                select(DiscoveryMission, CandidateProfile)
                .join(CandidateProfile, CandidateProfile.id == DiscoveryMission.candidate_profile_id)
                .where(DiscoveryMission.id == mission_id)
            ).first()
            if row is None:
                raise ValueError("persisted_discovery_mission_not_found")
            mission, profile = row
            if mission.status not in terminal_statuses:
                raise ValueError("discovery_mission_is_not_terminal")
            count = int(mission.cumulative_usable_opportunities or 0)
            if mission.status == "TARGET_MET":
                message = f"Job discovery reached its target and found {count} usable opportunities."
                title = "Job discovery completed"
            elif mission.status == "EXHAUSTED":
                message = f"Job discovery finished searching available sources and found {count} usable opportunities."
                title = "Job discovery completed"
            elif mission.status == "CANCELLED":
                message = "Job discovery was cancelled because its saved conditions changed."
                title = "Job discovery cancelled"
            else:
                message = "Job discovery could not complete. You can try again later."
                title = "Job discovery needs attention"
            _insert_notification(db, {
                "user_id": profile.user_id,
                "event_key": f"discovery-completed:{mission.id}",
                "event_type": "DISCOVERY_MISSION_COMPLETED",
                "title": title,
                "message": message,
                "resource_type": "DISCOVERY_MISSION",
                "resource_id": mission.id,
                "created_at": mission.completed_at or mission.updated_at,
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
