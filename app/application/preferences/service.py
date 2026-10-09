from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.candidates.service import CandidateService
from app.application.preferences.schemas import CandidatePreferencesData
from app.infrastructure.db.models import CandidatePreferences


class CandidatePreferencesService:
    def __init__(self, db: Session):
        self.db = db

    def _profile_id(self, user_id: UUID) -> UUID:
        profile = CandidateService(self.db).get_or_create_profile(user_id)
        return profile.id

    def get(self, user_id: UUID) -> tuple[int, CandidatePreferencesData]:
        profile_id = self._profile_id(user_id)
        row = self.db.scalar(
            select(CandidatePreferences).where(
                CandidatePreferences.candidate_profile_id == profile_id
            )
        )
        if row is None:
            return 0, CandidatePreferencesData.default_preferences()
        return row.revision, CandidatePreferencesData.model_validate(row.preferences)

    def put(
        self,
        user_id: UUID,
        expected_revision: int,
        preferences: CandidatePreferencesData,
    ) -> tuple[int, CandidatePreferencesData]:
        profile_id = self._profile_id(user_id)
        row = self.db.scalar(
            select(CandidatePreferences)
            .where(CandidatePreferences.candidate_profile_id == profile_id)
            .with_for_update()
        )
        if row is None:
            if expected_revision != 0:
                raise DomainError(
                    "REVISION_CONFLICT",
                    "Candidate preferences do not exist at the supplied revision.",
                    409,
                    {"current_revision": 0},
                )
            row = CandidatePreferences(
                candidate_profile_id=profile_id,
                revision=1,
                preferences=preferences.model_dump(by_alias=True, mode="json"),
            )
            self.db.add(row)
        else:
            if row.revision != expected_revision:
                raise DomainError(
                    "REVISION_CONFLICT",
                    "Candidate preferences have changed.",
                    409,
                    {"current_revision": row.revision},
                )
            row.preferences = preferences.model_dump(by_alias=True, mode="json")
            row.revision += 1

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            current = self.db.scalar(
                select(CandidatePreferences).where(
                    CandidatePreferences.candidate_profile_id == profile_id
                )
            )
            raise DomainError(
                "REVISION_CONFLICT",
                "Candidate preferences were concurrently created or updated.",
                409,
                {"current_revision": current.revision if current else 0},
            ) from exc
        self.db.refresh(row)
        return row.revision, CandidatePreferencesData.model_validate(row.preferences)
