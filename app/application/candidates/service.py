import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.candidates.schemas import EvidenceCreate
from app.domain.candidate.errors import InvalidEvidenceTransition
from app.domain.candidate.service import transition_evidence
from app.domain.candidate.claim_states import can_transition_claim
from app.domain.candidate.claim_support import claim_facts_are_supported
from app.infrastructure.db.models import (
    CandidateProfile, CandidateClaim, CandidateClaimEvidence, Evidence, EvidenceSource,
)


class CandidateService:
    def __init__(self, db: Session):
        self.db = db

    def _find_or_create_profile(self, user_id: uuid.UUID) -> tuple[CandidateProfile, bool]:
        profile = self.db.scalar(
            select(CandidateProfile).where(CandidateProfile.user_id == user_id)
        )
        if profile:
            return profile, False

        profile = CandidateProfile(user_id=user_id)
        self.db.add(profile)
        try:
            self.db.flush()
            return profile, True
        except IntegrityError:
            # A concurrent request may have created the unique profile first.
            self.db.rollback()
            profile = self.db.scalar(
                select(CandidateProfile).where(CandidateProfile.user_id == user_id)
            )
            if profile is None:
                raise
            return profile, False

    def get_or_create_profile(self, user_id: uuid.UUID) -> CandidateProfile:
        profile, created = self._find_or_create_profile(user_id)
        if created:
            # A GET request must not return a profile that disappears when its
            # request-scoped SQLAlchemy session closes.
            self.db.commit()
            self.db.refresh(profile)
        return profile

    def update_profile(
        self,
        user_id: uuid.UUID,
        expected_revision: int,
        sections: dict,
    ) -> CandidateProfile:
        profile = self.get_or_create_profile(user_id)
        profile = self.db.scalar(
            select(CandidateProfile)
            .where(CandidateProfile.id == profile.id)
            .with_for_update()
        )
        if profile is None:
            raise DomainError("NOT_FOUND", "Candidate profile not found", 404)
        if profile.revision != expected_revision:
            raise DomainError(
                "REVISION_CONFLICT",
                "Candidate profile has changed.",
                409,
                {"current_revision": profile.revision},
            )
        profile.profile_data = {**(profile.profile_data or {}), **sections}
        profile.revision += 1
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def create_evidence(self, user_id: uuid.UUID, data: EvidenceCreate) -> Evidence:
        # Keep profile, provenance source, and evidence creation in one transaction.
        profile, _ = self._find_or_create_profile(user_id)
        source = EvidenceSource(
            source_type=data.source_type,
            source_reference=data.source_reference,
            metadata_json=data.source_metadata,
        )
        self.db.add(source)
        self.db.flush()

        evidence = Evidence(
            candidate_profile_id=profile.id,
            evidence_source_id=source.id,
            evidence_type=data.evidence_type,
            content=data.content,
            status="DRAFT",
        )
        self.db.add(evidence)
        self.db.commit()
        self.db.refresh(evidence)
        return evidence

    def transition_evidence(
        self,
        user_id: uuid.UUID,
        evidence_id: uuid.UUID,
        target: str,
        expected_revision: int,
    ) -> Evidence:
        evidence = self.db.scalar(
            select(Evidence)
            .join(CandidateProfile)
            .where(
                Evidence.id == evidence_id,
                CandidateProfile.user_id == user_id,
            )
            .with_for_update()
        )
        if not evidence:
            raise DomainError("NOT_FOUND", "Evidence not found", 404)
        if evidence.revision != expected_revision:
            raise DomainError(
                "REVISION_CONFLICT",
                "Resource revision is stale",
                409,
                {"current_revision": evidence.revision},
            )
        if target == "ARCHIVED" and evidence.status == "APPROVED":
            approved_claim = self.db.scalar(
                select(CandidateClaim.id)
                .join(
                    CandidateClaimEvidence,
                    CandidateClaimEvidence.candidate_claim_id == CandidateClaim.id,
                )
                .where(
                    CandidateClaimEvidence.evidence_id == evidence.id,
                    CandidateClaim.status == "APPROVED",
                )
                .limit(1)
            )
            if approved_claim is not None:
                raise DomainError(
                    "EVIDENCE_SUPPORTS_APPROVED_CLAIM",
                    "Archive supporting claims before archiving evidence used by an approved claim",
                    409,
                )
        try:
            transition_evidence(evidence, target)
        except InvalidEvidenceTransition as exc:
            raise DomainError("INVALID_STATE_TRANSITION", str(exc), 409) from exc
        self.db.commit()
        self.db.refresh(evidence)
        return evidence


    def claim_evidence_ids(self, claim_id: uuid.UUID) -> list[uuid.UUID]:
        return list(self.db.scalars(
            select(CandidateClaimEvidence.evidence_id)
            .where(CandidateClaimEvidence.candidate_claim_id == claim_id)
            .order_by(CandidateClaimEvidence.created_at.asc())
        ).all())

    def list_claims(self, user_id: uuid.UUID) -> list[CandidateClaim]:
        return list(self.db.scalars(
            select(CandidateClaim)
            .join(CandidateProfile, CandidateProfile.id == CandidateClaim.candidate_profile_id)
            .where(CandidateProfile.user_id == user_id)
            .order_by(CandidateClaim.created_at.desc())
        ).all())

    def create_claim(self, user_id: uuid.UUID, data) -> CandidateClaim:
        profile, _ = self._find_or_create_profile(user_id)
        evidence_ids = list(data.evidence_ids)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise DomainError("DUPLICATE_EVIDENCE_LINK", "Evidence IDs must be unique", 422)
        if evidence_ids:
            owned_evidence = list(self.db.scalars(
                select(Evidence).where(
                    Evidence.candidate_profile_id == profile.id,
                    Evidence.id.in_(evidence_ids),
                )
            ).all())
            if len(owned_evidence) != len(evidence_ids):
                raise DomainError(
                    "EVIDENCE_NOT_FOUND",
                    "One or more evidence items do not belong to this candidate",
                    404,
                )

        claim = CandidateClaim(
            candidate_profile_id=profile.id,
            claim_type=data.claim_type,
            claim_content=data.claim_content,
            status="DRAFT",
        )
        self.db.add(claim)
        self.db.flush()
        for evidence_id in evidence_ids:
            self.db.add(CandidateClaimEvidence(
                candidate_claim_id=claim.id,
                evidence_id=evidence_id,
                relationship_type="SUPPORTS",
            ))
        self.db.commit()
        self.db.refresh(claim)
        return claim

    def transition_claim(
        self,
        user_id: uuid.UUID,
        claim_id: uuid.UUID,
        target: str,
        expected_revision: int,
    ) -> CandidateClaim:
        claim = self.db.scalar(
            select(CandidateClaim)
            .join(CandidateProfile, CandidateProfile.id == CandidateClaim.candidate_profile_id)
            .where(
                CandidateClaim.id == claim_id,
                CandidateProfile.user_id == user_id,
            )
            .with_for_update()
        )
        if claim is None:
            raise DomainError("NOT_FOUND", "Candidate claim not found", 404)
        if claim.revision != expected_revision:
            raise DomainError(
                "REVISION_CONFLICT",
                "Resource revision is stale",
                409,
                {"current_revision": claim.revision},
            )
        if not can_transition_claim(claim.status, target):
            raise DomainError(
                "INVALID_STATE_TRANSITION",
                f"Cannot transition claim from {claim.status} to {target}",
                409,
            )

        evidence_ids = self.claim_evidence_ids(claim.id)
        if target == "PENDING_REVIEW" and not evidence_ids:
            raise DomainError(
                "CLAIM_EVIDENCE_REQUIRED",
                "A claim needs supporting evidence before review",
                409,
            )
        if target == "APPROVED":
            now = datetime.now(timezone.utc)
            supporting_evidence = list(self.db.scalars(
                select(Evidence)
                .where(
                    Evidence.id.in_(evidence_ids),
                    Evidence.candidate_profile_id == claim.candidate_profile_id,
                    Evidence.status == "APPROVED",
                )
                .with_for_update()
            ).all()) if evidence_ids else []
            current_support = [
                item for item in supporting_evidence
                if (item.effective_from is None or item.effective_from <= now)
                and (item.effective_to is None or item.effective_to >= now)
                and (item.expires_at is None or item.expires_at > now)
            ]
            if not current_support or not claim_facts_are_supported(
                claim.claim_content,
                [item.content for item in current_support],
            ):
                raise DomainError(
                    "UNSUPPORTED_CLAIM",
                    "A claim requires current approved evidence that supports every stated fact",
                    409,
                )

        claim.status = target
        claim.revision += 1
        claim.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(claim)
        return claim
