"""Candidate-owned pathway persistence, AI suggestion capture, and approval lifecycle."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.ai.schemas import GenerateOptions, GenerateRequest
from app.application.ai.service import execute_ai_task
from app.application.candidates.service import CandidateService
from app.application.career_pathways.schemas import (
    CareerPathwayCreate, CareerPathwayFields, CareerPathwayUpdate,
)
from app.infrastructure.db.models import (
    CandidateClaim, CandidatePreferences, CandidateProfile, CareerPathway,
    CareerPathwayEvent, Evidence,
)


class CareerPathwayService:
    def __init__(self, db: Session):
        self.db = db

    def _profile(self, user_id: UUID) -> CandidateProfile:
        return CandidateService(self.db).get_or_create_profile(user_id)

    def _validate_evidence_refs(self, profile_id: UUID, evidence_refs: list[str]) -> list[str]:
        if not evidence_refs:
            return []
        ids = [UUID(value) for value in evidence_refs]
        now = datetime.now(timezone.utc)
        rows = self.db.scalars(
            select(Evidence).where(
                Evidence.id.in_(ids),
                Evidence.candidate_profile_id == profile_id,
                Evidence.status == "APPROVED",
            )
        ).all()
        valid = {
            row.id for row in rows
            if (row.effective_from is None or row.effective_from <= now)
            and (row.effective_to is None or row.effective_to > now)
            and (row.expires_at is None or row.expires_at > now)
        }
        if valid != set(ids):
            raise DomainError(
                "PATHWAY_EVIDENCE_NOT_AVAILABLE",
                "Supporting evidence must reference current, approved evidence owned by this candidate.",
                422,
            )
        return [str(value) for value in ids]

    def _event(
        self, *, pathway: CareerPathway, user_id: UUID, event_type: str,
        previous_status: str | None, reason: str | None = None, details: dict | None = None,
    ) -> None:
        self.db.add(CareerPathwayEvent(
            career_pathway_id=pathway.id,
            candidate_profile_id=pathway.candidate_profile_id,
            actor_user_id=user_id,
            event_type=event_type,
            previous_status=previous_status,
            new_status=pathway.approval_status,
            pathway_revision=pathway.revision,
            reason=reason,
            details=details or {},
            created_at=datetime.now(timezone.utc),
        ))

    def create_user_defined(self, user_id: UUID, data: CareerPathwayCreate) -> CareerPathway:
        profile = self._profile(user_id)
        evidence_refs = self._validate_evidence_refs(profile.id, data.supporting_evidence_refs)
        now = datetime.now(timezone.utc)
        pathway = CareerPathway(
            candidate_profile_id=profile.id,
            title=data.title,
            role_family=data.role_family,
            target_roles=data.target_roles,
            target_seniority=data.target_seniority,
            domains=data.domains,
            supporting_evidence_refs=evidence_refs,
            gaps=data.gaps,
            rationale=data.rationale,
            confidence=data.confidence,
            source_type="USER_DEFINED",
            approval_status="APPROVED",
            approved_at=now,
            approved_by_user_id=user_id,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        self.db.add(pathway)
        self.db.flush()
        self._event(
            pathway=pathway, user_id=user_id, event_type="CREATED",
            previous_status=None, reason="Candidate created a user-defined pathway",
            details={"source_type": "USER_DEFINED"},
        )
        self.db.commit()
        self.db.refresh(pathway)
        return pathway

    async def suggest(
        self, user_id: UUID, *, instruction: str | None = None,
        request_id: str | None = None,
    ) -> list[CareerPathway]:
        profile = self._profile(user_id)
        now = datetime.now(timezone.utc)
        evidence_rows = self.db.scalars(
            select(Evidence).where(
                Evidence.candidate_profile_id == profile.id,
                Evidence.status == "APPROVED",
            ).order_by(Evidence.updated_at.desc(), Evidence.id)
        ).all()
        evidence_rows = [
            row for row in evidence_rows
            if (row.effective_from is None or row.effective_from <= now)
            and (row.effective_to is None or row.effective_to > now)
            and (row.expires_at is None or row.expires_at > now)
        ]
        if not evidence_rows:
            raise DomainError(
                "APPROVED_EVIDENCE_REQUIRED",
                "Approve at least one current evidence item before requesting AI pathway suggestions.",
                409,
            )
        preferences_row = self.db.scalar(select(CandidatePreferences).where(
            CandidatePreferences.candidate_profile_id == profile.id
        ))
        preferences = preferences_row.preferences if preferences_row else {}
        target_roles = preferences.get("targetRoles", preferences.get("target_roles", []))
        prompt_data = {
            "candidate_profile": profile.profile_data or {},
            "approved_candidate_evidence": [
                {"id": str(row.id), "type": row.evidence_type, "revision": row.revision, "content": row.content}
                for row in evidence_rows
            ],
            "current_target_roles": target_roles if isinstance(target_roles, list) else [],
            "instruction": instruction,
        }
        result = await execute_ai_task(
            GenerateRequest(
                task="career_pathway_discovery",
                prompt=json.dumps(prompt_data, ensure_ascii=False, separators=(",", ":")),
                options=GenerateOptions(max_tokens=2048),
            ),
            db=self.db,
            user_id=user_id,
            request_id=request_id,
        )
        parsed = result.parsed_data
        if not isinstance(parsed, dict) or not isinstance(parsed.get("pathways"), list):
            raise DomainError(
                "PATHWAY_AI_OUTPUT_INVALID",
                "The pathway suggestion response was not a valid structured result.",
                502,
            )
        created: list[CareerPathway] = []
        for item in parsed["pathways"]:
            fields = CareerPathwayFields.model_validate({
                "title": item["title"],
                "role_family": item["role_family"],
                "target_roles": item["target_roles"],
                "target_seniority": item.get("target_seniority", []),
                "domains": item.get("domains", []),
                "supporting_evidence_refs": item.get("supporting_evidence_refs", []),
                "gaps": item.get("gaps", []),
                "rationale": item["rationale"],
                "confidence": item["confidence"],
            })
            evidence_refs = self._validate_evidence_refs(profile.id, fields.supporting_evidence_refs)
            pathway = CareerPathway(
                candidate_profile_id=profile.id,
                title=fields.title,
                role_family=fields.role_family,
                target_roles=fields.target_roles,
                target_seniority=fields.target_seniority,
                domains=fields.domains,
                supporting_evidence_refs=evidence_refs,
                gaps=fields.gaps,
                rationale=fields.rationale,
                confidence=fields.confidence,
                source_type="AI_SUGGESTED",
                approval_status="PENDING_REVIEW",
                approved_at=None,
                approved_by_user_id=None,
                revision=1,
                created_at=now,
                updated_at=now,
            )
            self.db.add(pathway)
            self.db.flush()
            self._event(
                pathway=pathway, user_id=user_id, event_type="SUGGESTED",
                previous_status=None,
                reason="AI-generated suggestion requires candidate review",
                details={
                    "source_type": "AI_SUGGESTED",
                    "ai_execution_id": str(result.execution_id) if result.execution_id else None,
                    "caveats": parsed.get("caveats", []),
                },
            )
            created.append(pathway)
        self.db.commit()
        for pathway in created:
            self.db.refresh(pathway)
        return created

    def list(self, user_id: UUID, *, approval_status: str | None = None,
             source_type: str | None = None) -> list[CareerPathway]:
        profile = self._profile(user_id)
        query = select(CareerPathway).where(CareerPathway.candidate_profile_id == profile.id)
        if approval_status is not None:
            query = query.where(CareerPathway.approval_status == approval_status)
        if source_type is not None:
            query = query.where(CareerPathway.source_type == source_type)
        return list(self.db.scalars(
            query.order_by(CareerPathway.updated_at.desc(), CareerPathway.id)
        ).all())

    def get(self, user_id: UUID, pathway_id: UUID) -> CareerPathway:
        profile = self._profile(user_id)
        pathway = self.db.scalar(select(CareerPathway).where(
            CareerPathway.id == pathway_id,
            CareerPathway.candidate_profile_id == profile.id,
        ))
        if pathway is None:
            raise DomainError("CAREER_PATHWAY_NOT_FOUND", "Career pathway not found.", 404)
        return pathway

    def update(self, user_id: UUID, pathway_id: UUID, data: CareerPathwayUpdate) -> CareerPathway:
        pathway = self.db.scalar(select(CareerPathway).where(
            CareerPathway.id == pathway_id,
            CareerPathway.candidate_profile_id == self._profile(user_id).id,
        ).with_for_update())
        if pathway is None:
            raise DomainError("CAREER_PATHWAY_NOT_FOUND", "Career pathway not found.", 404)
        if pathway.source_type != "USER_DEFINED":
            raise DomainError(
                "AI_SUGGESTION_IMMUTABLE",
                "AI-suggested pathways cannot be edited; approve, reject, or create a user-defined pathway instead.",
                409,
            )
        if pathway.approval_status == "RETIRED":
            raise DomainError("CAREER_PATHWAY_RETIRED", "Retired pathways cannot be edited.", 409)
        if data.expected_revision != pathway.revision:
            raise DomainError(
                "REVISION_CONFLICT", "Career pathway has changed.", 409,
                {"current_revision": pathway.revision},
            )
        evidence_refs = self._validate_evidence_refs(
            pathway.candidate_profile_id, data.supporting_evidence_refs
        )
        previous_status = pathway.approval_status
        for field in ("title", "role_family", "target_roles", "target_seniority", "domains", "gaps", "rationale", "confidence"):
            setattr(pathway, field, getattr(data, field))
        pathway.supporting_evidence_refs = evidence_refs
        pathway.revision += 1
        pathway.updated_at = datetime.now(timezone.utc)
        self._event(
            pathway=pathway, user_id=user_id, event_type="EDITED",
            previous_status=previous_status,
            reason="Candidate edited a user-defined pathway",
            details={"source_type": pathway.source_type},
        )
        self.db.commit()
        self.db.refresh(pathway)
        return pathway

    def transition(
        self, user_id: UUID, pathway_id: UUID, action: str,
        expected_revision: int, reason: str,
    ) -> CareerPathway:
        pathway = self.db.scalar(select(CareerPathway).where(
            CareerPathway.id == pathway_id,
            CareerPathway.candidate_profile_id == self._profile(user_id).id,
        ).with_for_update())
        if pathway is None:
            raise DomainError("CAREER_PATHWAY_NOT_FOUND", "Career pathway not found.", 404)
        if expected_revision != pathway.revision:
            raise DomainError(
                "REVISION_CONFLICT", "Career pathway has changed.", 409,
                {"current_revision": pathway.revision},
            )
        previous = pathway.approval_status
        if action in {"approve", "reject"}:
            if pathway.source_type != "AI_SUGGESTED" or previous != "PENDING_REVIEW":
                raise DomainError(
                    "PATHWAY_REVIEW_NOT_ALLOWED",
                    "Only pending AI suggestions can be approved or rejected.",
                    409,
                )
            pathway.approval_status = "APPROVED" if action == "approve" else "REJECTED"
            pathway.approved_at = datetime.now(timezone.utc) if action == "approve" else None
            pathway.approved_by_user_id = user_id if action == "approve" else None
            event_type = "APPROVED" if action == "approve" else "REJECTED"
        elif action == "retire":
            if previous == "RETIRED":
                raise DomainError("CAREER_PATHWAY_ALREADY_RETIRED", "Career pathway is already retired.", 409)
            pathway.approval_status = "RETIRED"
            event_type = "RETIRED"
        else:
            raise DomainError("INVALID_PATHWAY_ACTION", "Unsupported pathway action.", 422)
        pathway.revision += 1
        pathway.updated_at = datetime.now(timezone.utc)
        self._event(
            pathway=pathway, user_id=user_id, event_type=event_type,
            previous_status=previous, reason=reason,
            details={"source_type": pathway.source_type, "action": action},
        )
        self.db.commit()
        self.db.refresh(pathway)
        return pathway

    def events(self, user_id: UUID, pathway_id: UUID) -> list[CareerPathwayEvent]:
        pathway = self.get(user_id, pathway_id)
        return list(self.db.scalars(
            select(CareerPathwayEvent).where(
                CareerPathwayEvent.career_pathway_id == pathway.id,
                CareerPathwayEvent.candidate_profile_id == pathway.candidate_profile_id,
            ).order_by(CareerPathwayEvent.created_at, CareerPathwayEvent.id)
        ).all())
