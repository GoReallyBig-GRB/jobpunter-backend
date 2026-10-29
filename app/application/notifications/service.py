from datetime import datetime, timezone
from email.message import EmailMessage
import smtplib
import ssl
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings
from app.application.notifications.smtp_config import decrypt_smtp_password

from app.infrastructure.db.models import (
    Application,
    ApplicationEvent,
    CandidateEvaluation,
    CandidateProfile,
    DiscoveryMission,
    Job,
    Notification,
    NotificationEmailDelivery,
    NotificationEmailPreference,
    OutboxEvent,
    Punt,
    User,
    SMTPProviderConfiguration,
)


def _insert_notification(db: Session, values: dict) -> None:
    """Persist an in-app alert and, only when opted in, durable email work atomically."""
    inserted_id = db.execute(
        pg_insert(Notification)
        .values(**values)
        .on_conflict_do_nothing(constraint="uq_notifications_event_key")
        .returning(Notification.id)
    ).scalar_one_or_none()
    if inserted_id is None:
        db.commit()
        return

    user_id = values["user_id"]
    preference = db.get(NotificationEmailPreference, user_id)
    user = db.get(User, user_id)
    if preference is not None and preference.enabled:
        if user is None or not user.email or not user.email_verified:
            status = "SKIPPED_UNVERIFIED"
        else:
            status = "PENDING"
    else:
        status = "DISABLED"

    db.add(NotificationEmailDelivery(
        notification_id=inserted_id,
        status=status,
        attempt_count=0,
        last_error=None,
        sent_at=None,
        updated_at=datetime.now(timezone.utc),
    ))
    if status == "PENDING":
        db.add(OutboxEvent(
            event_type="NOTIFICATION_EMAIL_REQUESTED",
            aggregate_type="Notification",
            aggregate_id=inserted_id,
            payload={"notification_id": str(inserted_id)},
            status="PENDING",
            attempt_count=0,
            available_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        ))
    db.commit()


def notification_email_delivery_handler(session_factory: sessionmaker):
    """Send opted-in email, persist each attempt outcome, and raise failures for bounded outbox retry."""
    def handle(payload: dict) -> None:
        try:
            notification_id = UUID(str(payload["notification_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid_notification_email_payload") from exc

        with session_factory() as db:
            row = db.execute(
                select(NotificationEmailDelivery, Notification, User)
                .join(Notification, Notification.id == NotificationEmailDelivery.notification_id)
                .join(User, User.id == Notification.user_id)
                .where(NotificationEmailDelivery.notification_id == notification_id)
                .with_for_update()
            ).first()
            if row is None:
                raise ValueError("notification_email_delivery_not_found")
            delivery, notification, user = row
            if delivery.status == "SENT":
                return
            preference = db.get(NotificationEmailPreference, user.id)
            if preference is None or not preference.enabled:
                delivery.status = "DISABLED"
                delivery.last_error = None
                delivery.updated_at = datetime.now(timezone.utc)
                db.commit()
                return
            if not user.email or not user.email_verified:
                delivery.status = "SKIPPED_UNVERIFIED"
                delivery.last_error = None
                delivery.updated_at = datetime.now(timezone.utc)
                db.commit()
                return

            smtp_config = db.scalar(
                select(SMTPProviderConfiguration)
                .where(SMTPProviderConfiguration.status == "PUBLISHED")
                .order_by(SMTPProviderConfiguration.version.desc())
                .limit(1)
            )
            settings = get_settings()
            if smtp_config is not None:
                try:
                    smtp_password = decrypt_smtp_password(smtp_config.encrypted_password)
                except RuntimeError:
                    # A broken active secret must not strand delivery if a known-good
                    # retired version remains decryptable with the current key.
                    smtp_config = db.scalar(
                        select(SMTPProviderConfiguration)
                        .where(SMTPProviderConfiguration.status == "RETIRED")
                        .order_by(SMTPProviderConfiguration.version.desc())
                        .limit(1)
                    )
                    try:
                        smtp_password = decrypt_smtp_password(
                            smtp_config.encrypted_password if smtp_config else None
                        )
                    except RuntimeError:
                        smtp_config = None
                        smtp_password = None
                if smtp_config is not None:
                    smtp_host = smtp_config.host
                    smtp_port = smtp_config.port
                    smtp_use_tls = smtp_config.use_tls
                    smtp_username = smtp_config.username
                    smtp_from_email = smtp_config.from_email
                    smtp_timeout = 10
                    event_policy = smtp_config.event_policy or {}
                    if event_policy.get(notification.event_type) is False:
                        delivery.status = "DISABLED"
                        delivery.last_error = None
                        delivery.updated_at = datetime.now(timezone.utc)
                        db.commit()
                        return
                    retry_limit = smtp_config.retry_limit
                else:
                    smtp_host = smtp_port = smtp_use_tls = smtp_username = smtp_password = smtp_from_email = None
                    smtp_timeout = 10
                    retry_limit = 0
            else:
                # Backward-compatible startup fallback until an Admin configuration is published.
                smtp_host, smtp_port, smtp_use_tls = settings.smtp_host, settings.smtp_port, settings.smtp_use_tls
                smtp_username, smtp_password = settings.smtp_username, settings.smtp_password
                smtp_from_email, smtp_timeout = settings.smtp_from_email, settings.smtp_timeout_seconds
                retry_limit = 5
            if not smtp_host or not smtp_from_email:
                delivery.status = "SKIPPED_UNCONFIGURED"
                delivery.last_error = "smtp_provider_not_configured"
                delivery.updated_at = datetime.now(timezone.utc)
                db.commit()
                return
            if delivery.attempt_count >= retry_limit:
                delivery.status = "FAILED"
                delivery.last_error = "smtp_retry_limit_reached"
                delivery.updated_at = datetime.now(timezone.utc)
                db.commit()
                return

            delivery.attempt_count += 1
            delivery.status = "SENDING"
            delivery.last_error = None
            delivery.updated_at = datetime.now(timezone.utc)
            db.commit()
            recipient = user.email
            title = notification.title
            message = notification.message
            stable_message_id = f"<jobpunter-notification-{notification_id}@jobpunter.local>"

        email = EmailMessage()
        email["Subject"] = title
        email["From"] = smtp_from_email
        email["To"] = recipient
        email["Message-ID"] = stable_message_id
        email.set_content(message)

        try:
            with smtplib.SMTP(
                smtp_host,
                smtp_port,
                timeout=smtp_timeout,
            ) as client:
                if smtp_use_tls:
                    client.starttls(context=ssl.create_default_context())
                if smtp_username and smtp_password:
                    client.login(smtp_username, smtp_password)
                client.send_message(email)
        except Exception as exc:
            with session_factory() as db:
                delivery = db.scalar(
                    select(NotificationEmailDelivery)
                    .where(NotificationEmailDelivery.notification_id == notification_id)
                    .with_for_update()
                )
                if delivery is not None and delivery.status != "SENT":
                    delivery.status = "FAILED"
                    # Do not persist SMTP response text or credentials in operational records.
                    delivery.last_error = type(exc).__name__[:64]
                    delivery.updated_at = datetime.now(timezone.utc)
                    db.commit()
            raise RuntimeError("notification_email_delivery_failed") from exc

        with session_factory() as db:
            delivery = db.scalar(
                select(NotificationEmailDelivery)
                .where(NotificationEmailDelivery.notification_id == notification_id)
                .with_for_update()
            )
            if delivery is not None and delivery.status != "SENT":
                delivery.status = "SENT"
                delivery.last_error = None
                delivery.sent_at = datetime.now(timezone.utc)
                delivery.updated_at = delivery.sent_at
                db.commit()
    return handle


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
                message = (
                    "Job discovery was cancelled by an administrator."
                    if mission.error_code == "CANCELLED_BY_ADMIN"
                    else "Job discovery was cancelled because its saved conditions changed."
                )
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
