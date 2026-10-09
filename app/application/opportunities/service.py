import uuid
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.common.outbox import OutboxService
from app.domain.opportunities.service import qualify_punt, transition_application
from app.infrastructure.db.models import (
    Application, ApplicationEvent, CandidateEvaluation, CandidateProfile,
    Punt, PuntEvaluation,
)


class OpportunityService:
    """Candidate-scoped persistence for evaluations, Punts and applications."""

    def __init__(self, db: Session):
        self.db = db

    def _profile(self, user_id: UUID) -> CandidateProfile:
        profile = self.db.scalar(
            select(CandidateProfile).where(CandidateProfile.user_id == user_id)
        )
        if profile is None:
            raise DomainError("CANDIDATE_PROFILE_NOT_FOUND", "Candidate profile not found", 404)
        return profile

    def list_evaluations(self, user_id: UUID) -> list[CandidateEvaluation]:
        profile = self._profile(user_id)
        return list(self.db.scalars(
            select(CandidateEvaluation)
            .where(CandidateEvaluation.candidate_profile_id == profile.id)
            .order_by(CandidateEvaluation.evaluated_at.desc())
        ).all())

    def create_punt(self, user_id: UUID, evaluation_id: UUID) -> Punt:
        profile = self._profile(user_id)
        evaluation = self.db.scalar(
            select(CandidateEvaluation)
            .where(
                CandidateEvaluation.id == evaluation_id,
                CandidateEvaluation.candidate_profile_id == profile.id,
            )
            .with_for_update()
        )
        if evaluation is None:
            raise DomainError("EVALUATION_NOT_FOUND", "Evaluation not found", 404)
        try:
            qualify_punt(evaluation)
        except ValueError as exc:
            raise DomainError("EVALUATION_NOT_QUALIFIED", str(exc), 409) from exc

        now = datetime.now(timezone.utc)
        inserted_id = self.db.scalar(
            pg_insert(Punt)
            .values(
                candidate_profile_id=profile.id,
                job_id=evaluation.job_id,
                status="ACTIVE",
                qualified_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_punt_candidate_job")
            .returning(Punt.id)
        )
        created = inserted_id is not None
        punt = self.db.scalar(
            select(Punt).where(
                Punt.candidate_profile_id == profile.id,
                Punt.job_id == evaluation.job_id,
            ).with_for_update()
        )
        if punt is None:
            raise RuntimeError("Punt insert conflicted but the canonical Punt could not be reloaded")

        linked = self.db.get(PuntEvaluation, (punt.id, evaluation.id))
        if linked is None:
            self.db.add(PuntEvaluation(punt_id=punt.id, evaluation_id=evaluation.id))
        if created:
            OutboxService(self.db).append(
                event_type="PUNT_QUALIFIED",
                aggregate_type="Punt",
                aggregate_id=punt.id,
                payload={"punt_id": str(punt.id), "candidate_profile_id": str(profile.id),
                         "job_id": str(evaluation.job_id), "evaluation_id": str(evaluation.id)},
            )
        self.db.commit()
        self.db.refresh(punt)
        return punt

    def list_punts(self, user_id: UUID) -> list[Punt]:
        profile = self._profile(user_id)
        return list(self.db.scalars(
            select(Punt).where(Punt.candidate_profile_id == profile.id)
            .order_by(Punt.qualified_at.desc())
        ).all())

    def create_application(self, user_id: UUID, punt_id: UUID) -> Application:
        profile = self._profile(user_id)
        punt = self.db.scalar(
            select(Punt).where(
                Punt.id == punt_id, Punt.candidate_profile_id == profile.id
            ).with_for_update()
        )
        if punt is None:
            raise DomainError("PUNT_NOT_FOUND", "Punt not found", 404)
        now = datetime.now(timezone.utc)
        proposed_id = uuid.uuid4()
        inserted_id = self.db.scalar(
            pg_insert(Application)
            .values(
                id=proposed_id,
                candidate_profile_id=profile.id,
                job_id=punt.job_id,
                punt_id=punt.id,
                status="PLANNED",
                revision=1,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_application_candidate_job")
            .returning(Application.id)
        )
        created = inserted_id is not None
        application = self.db.scalar(
            select(Application).where(
                Application.candidate_profile_id == profile.id,
                Application.job_id == punt.job_id,
            ).with_for_update()
        )
        if application is None:
            raise RuntimeError("Application insert conflicted but the canonical application could not be reloaded")
        if created:
            self.db.add(ApplicationEvent(
                application_id=application.id,
                event_type="APPLICATION_CREATED",
                from_status=None,
                to_status="PLANNED",
                actor_type="CANDIDATE",
                actor_id=profile.user_id,
                occurred_at=now,
                metadata_json={"punt_id": str(punt.id)},
            ))
            OutboxService(self.db).append(
                event_type="APPLICATION_CREATED",
                aggregate_type="Application",
                aggregate_id=application.id,
                payload={"application_id": str(application.id),
                         "candidate_profile_id": str(profile.id), "job_id": str(punt.job_id)},
            )
        self.db.commit()
        self.db.refresh(application)
        return application

    def list_applications(self, user_id: UUID) -> list[Application]:
        profile = self._profile(user_id)
        return list(self.db.scalars(
            select(Application).where(Application.candidate_profile_id == profile.id)
            .order_by(Application.updated_at.desc())
        ).all())

    def transition_application(
        self, user_id: UUID, application_id: UUID, target_status: str, expected_revision: int
    ) -> Application:
        profile = self._profile(user_id)
        application = self.db.scalar(
            select(Application).where(
                Application.id == application_id,
                Application.candidate_profile_id == profile.id,
            ).with_for_update()
        )
        if application is None:
            raise DomainError("APPLICATION_NOT_FOUND", "Application not found", 404)
        if application.revision != expected_revision:
            raise DomainError(
                "REVISION_CONFLICT", "Resource revision is stale", 409,
                {"current_revision": application.revision},
            )
        now = datetime.now(timezone.utc)
        previous = application.status
        try:
            event_data = transition_application(
                application, target_status, actor_type="CANDIDATE", now=now
            )
        except ValueError as exc:
            raise DomainError("INVALID_STATE_TRANSITION", str(exc), 409) from exc
        self.db.add(ApplicationEvent(
            application_id=application.id,
            event_type=event_data["event_type"],
            from_status=previous,
            to_status=application.status,
            actor_type="CANDIDATE",
            actor_id=profile.user_id,
            occurred_at=now,
            metadata_json={},
        ))
        OutboxService(self.db).append(
            event_type="APPLICATION_STATUS_CHANGED",
            aggregate_type="Application",
            aggregate_id=application.id,
            payload={"application_id": str(application.id), "from_status": previous,
                     "to_status": application.status, "revision": application.revision},
        )
        self.db.commit()
        self.db.refresh(application)
        return application
