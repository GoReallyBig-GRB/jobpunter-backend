import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.candidates.schemas import EvidenceCreate
from app.domain.candidate.errors import InvalidEvidenceTransition
from app.domain.candidate.service import transition_evidence
from app.domain.candidate.claim_states import can_transition_claim
from app.domain.candidate.claim_support import claim_fact_conflicts_with_evidence, claim_facts_are_supported
from app.domain.candidate.provenance import source_excerpt_is_verifiable
from app.infrastructure.db.models import (
    CandidateProfile, CandidateClaim, CandidateClaimEvidence, CandidateProfileFactProvenance, DocumentImport, Evidence, EvidenceSource,
)



def _iter_profile_fact_values(value, path: str = ""):
    """Yield every supplied fact, retaining explicit empty-section clear operations."""
    if isinstance(value, dict):
        if not value:
            yield path, value
            return
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _iter_profile_fact_values(child, child_path)
        return
    if isinstance(value, list):
        if not value:
            yield path, value
            return
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            yield from _iter_profile_fact_values(child, child_path)
        return
    # A null leaf here was explicitly supplied by the PATCH payload and records a
    # candidate-requested clear; omitted fields never reach this traversal.
    yield path, value


def _same_provenance_value(left, right) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, str):
        return left.strip().casefold() == right.strip().casefold()
    return left == right


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
        fact_sources: dict | None = None,
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
        next_revision = profile.revision + 1
        now = datetime.now(timezone.utc)
        supplied_facts = {}
        for section_name, section_value in sections.items():
            for fact_path, fact_value in _iter_profile_fact_values(section_value):
                relative_path = fact_path
                if relative_path.startswith("["):
                    full_path = f"{section_name}{relative_path}"
                elif relative_path:
                    full_path = f"{section_name}.{relative_path}"
                else:
                    full_path = section_name
                supplied_facts[full_path] = (
                    section_name, relative_path or section_name, fact_value
                )

        resolved_sources = {}
        for full_path, source in (fact_sources or {}).items():
            if full_path not in supplied_facts:
                raise DomainError(
                    "PROVENANCE_FACT_PATH_INVALID",
                    "A fact source must refer to a fact supplied in this profile update.",
                    422,
                    {"fact_path": full_path},
                )
            document_import_id = source.get("document_import_id")
            field_name = str(source.get("field_name") or "").strip()
            import_record = self.db.scalar(
                select(DocumentImport)
                .where(
                    DocumentImport.id == document_import_id,
                    DocumentImport.candidate_profile_id == profile.id,
                )
                .with_for_update()
            )
            if (
                import_record is None
                or import_record.status != "SAVED"
                or import_record.confirmed_by_user_id != user_id
                or import_record.confirmed_at is None
                or import_record.saved_evidence_id is None
            ):
                raise DomainError(
                    "PROVENANCE_SOURCE_NOT_CONFIRMED",
                    "Profile facts can only reference a document import saved after this candidate's confirmation.",
                    409,
                )
            imported_fields = (
                import_record.extracted_fields.get("fields", [])
                if isinstance(import_record.extracted_fields, dict)
                else []
            )
            matching_field = next((
                field for field in imported_fields
                if isinstance(field, dict)
                and str(field.get("name") or "").strip().casefold() == field_name.casefold()
                and "value" in field
                and _same_provenance_value(field.get("value"), supplied_facts[full_path][2])
            ), None) if isinstance(imported_fields, list) else None
            if matching_field is None:
                raise DomainError(
                    "PROVENANCE_FACT_MISMATCH",
                    "The referenced imported field does not match the supplied profile fact.",
                    422,
                    {"fact_path": full_path, "field_name": field_name},
                )
            if not source_excerpt_is_verifiable(
                import_record.extracted_text, matching_field.get("source_text")
            ):
                raise DomainError(
                    "PROVENANCE_SOURCE_EXCERPT_UNVERIFIABLE",
                    "The imported field has no source excerpt that can be verified against the parsed document.",
                    422,
                    {"fact_path": full_path, "field_name": field_name},
                )
            source_evidence = self.db.get(Evidence, import_record.saved_evidence_id)
            if source_evidence is None or source_evidence.candidate_profile_id != profile.id:
                raise DomainError(
                    "PROVENANCE_SOURCE_INTEGRITY_FAILED",
                    "The imported fact's saved evidence could not be verified.",
                    409,
                )
            resolved_sources[full_path] = (import_record, source_evidence, matching_field)

        profile.profile_data = {**(profile.profile_data or {}), **sections}
        profile.revision = next_revision
        for full_path, (section_name, fact_path, fact_value) in supplied_facts.items():
            source = resolved_sources.get(full_path)
            if source is None:
                source_type = "CANDIDATE_INPUT"
                source_reference = f"candidate-profile-patch:{profile.id}:{next_revision}"
                evidence_id = None
                document_import_id = None
                source_excerpt = None
                confidence = None
                confirmed_at = now
                confirmed_by_user_id = user_id
            else:
                import_record, source_evidence, imported_field = source
                source_type = "DOCUMENT_IMPORT"
                source_reference = f"document-import:{import_record.id}"
                evidence_id = source_evidence.id
                document_import_id = import_record.id
                excerpt = imported_field.get("source_text")
                source_excerpt = excerpt if isinstance(excerpt, str) else None
                confidence = imported_field.get("confidence")
                confirmed_at = import_record.confirmed_at
                confirmed_by_user_id = import_record.confirmed_by_user_id
            self.db.add(CandidateProfileFactProvenance(
                candidate_profile_id=profile.id,
                section=section_name,
                fact_path=fact_path or section_name,
                fact_value=fact_value,
                profile_revision=next_revision,
                source_type=source_type,
                source_reference=source_reference,
                evidence_id=evidence_id,
                document_import_id=document_import_id,
                source_excerpt=source_excerpt,
                confidence=confidence,
                confirmed_at=confirmed_at,
                confirmed_by_user_id=confirmed_by_user_id,
                created_at=now,
            ))
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def list_profile_fact_provenance(
        self, user_id: uuid.UUID, section: str | None = None
    ) -> list[CandidateProfileFactProvenance]:
        query = (
            select(CandidateProfileFactProvenance)
            .join(
                CandidateProfile,
                CandidateProfile.id == CandidateProfileFactProvenance.candidate_profile_id,
            )
            .where(CandidateProfile.user_id == user_id)
        )
        if section is not None:
            query = query.where(CandidateProfileFactProvenance.section == section)
        query = query.order_by(
            CandidateProfileFactProvenance.created_at.asc(),
            CandidateProfileFactProvenance.id.asc(),
        )
        return list(self.db.scalars(query).all())

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

    def update_claim(
        self,
        user_id: uuid.UUID,
        claim_id: uuid.UUID,
        data,
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
        if claim.revision != data.expected_revision:
            raise DomainError(
                "REVISION_CONFLICT",
                "Candidate claim has changed.",
                409,
                {"current_revision": claim.revision},
            )
        if claim.status not in {"DRAFT", "PENDING_REVIEW"}:
            raise DomainError(
                "CLAIM_NOT_EDITABLE",
                "Only draft or pending-review claims can be corrected; approved claims are immutable.",
                409,
            )

        supplied = data.model_fields_set
        replacement_evidence_ids = data.evidence_ids if "evidence_ids" in supplied else None
        if replacement_evidence_ids is not None:
            if len(set(replacement_evidence_ids)) != len(replacement_evidence_ids):
                raise DomainError("DUPLICATE_EVIDENCE_LINK", "Evidence IDs must be unique", 422)
            owned_evidence = list(self.db.scalars(
                select(Evidence)
                .where(
                    Evidence.candidate_profile_id == claim.candidate_profile_id,
                    Evidence.id.in_(replacement_evidence_ids),
                )
                .with_for_update()
            ).all()) if replacement_evidence_ids else []
            if len(owned_evidence) != len(replacement_evidence_ids):
                raise DomainError(
                    "EVIDENCE_NOT_FOUND",
                    "One or more evidence items do not belong to this candidate",
                    404,
                )

        if "claim_content" in supplied:
            claim.claim_content = data.claim_content
        if replacement_evidence_ids is not None:
            self.db.execute(delete(CandidateClaimEvidence).where(
                CandidateClaimEvidence.candidate_claim_id == claim.id,
            ))
            for evidence_id in replacement_evidence_ids:
                self.db.add(CandidateClaimEvidence(
                    candidate_claim_id=claim.id,
                    evidence_id=evidence_id,
                    relationship_type="SUPPORTS",
                ))
        if claim.status == "PENDING_REVIEW":
            # Any factual correction invalidates the prior review decision.
            claim.status = "DRAFT"
        claim.revision += 1
        claim.updated_at = datetime.now(timezone.utc)
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
            current_evidence_contents = [item.content for item in current_support]
            conflicts = claim_fact_conflicts_with_evidence(
                claim.claim_content,
                current_evidence_contents,
            ) if current_support else []
            if conflicts:
                raise DomainError(
                    "CONTRADICTED_CLAIM",
                    "A claim conflicts with values in current approved supporting evidence; resolve the conflict before approval.",
                    409,
                    {"conflicting_facts": conflicts},
                )
            if not current_support or not claim_facts_are_supported(
                claim.claim_content,
                current_evidence_contents,
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
