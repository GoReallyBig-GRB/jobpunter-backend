"""Candidate-scoped, append-only application package creation and retrieval."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.application_packages.schemas import ApplicationPackageCreate
from app.infrastructure.db.models import (
    AIExecution, Application, ApplicationPackage, ApplicationPackageItem,
    CandidateEvaluation, CandidateProfile, Evidence, Job, JobDescriptionVersion,
)


class ApplicationPackageService:
    def __init__(self, db: Session):
        self.db = db

    def _owned_application(self, user_id: UUID, application_id: UUID) -> tuple[CandidateProfile, Application]:
        profile = self.db.scalar(
            select(CandidateProfile).where(CandidateProfile.user_id == user_id)
        )
        if profile is None:
            raise DomainError("CANDIDATE_PROFILE_NOT_FOUND", "Candidate profile not found", 404)
        application = self.db.scalar(
            select(Application).where(
                Application.id == application_id,
                Application.candidate_profile_id == profile.id,
            ).with_for_update()
        )
        if application is None:
            raise DomainError("APPLICATION_NOT_FOUND", "Application not found", 404)
        return profile, application

    @staticmethod
    def _hash_content(content: dict) -> str:
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _source_snapshot(self, profile: CandidateProfile, application: Application, job: Job) -> dict:
        evaluation = self.db.scalar(
            select(CandidateEvaluation).where(
                CandidateEvaluation.candidate_profile_id == profile.id,
                CandidateEvaluation.job_id == application.job_id,
            ).order_by(CandidateEvaluation.evaluated_at.desc()).limit(1)
        )
        jd = None
        if evaluation and evaluation.job_description_version_id:
            version = self.db.get(JobDescriptionVersion, evaluation.job_description_version_id)
            if version:
                jd = {
                    "id": str(version.id),
                    "version_number": version.version_number,
                    "content_hash": version.content_hash,
                    "completeness_status": version.completeness_status,
                }
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "application": {
                "id": str(application.id),
                "revision": application.revision,
                "status": application.status,
                "job_id": str(application.job_id),
            },
            "candidate_profile": {
                "id": str(profile.id),
                "revision": profile.revision,
                "status": profile.status,
            },
            "job": {
                "id": str(job.id),
                "revision": job.revision,
                "title": job.canonical_title,
                "company": job.company_name,
                "location": {
                    "country": job.location_country,
                    "region": job.location_region,
                    "city": job.location_city,
                },
                "work_mode": job.work_mode,
                "remote_eligibility": job.remote_eligibility,
                "application_url": job.application_url,
            },
            "job_description": jd,
            "evaluation": ({
                "id": str(evaluation.id),
                "version": evaluation.evaluation_version,
                "outcome": evaluation.outcome,
                "policy_version": evaluation.policy_version,
                "evaluated_at": evaluation.evaluated_at.isoformat(),
                "input_snapshot": evaluation.input_snapshot,
            } if evaluation else None),
        }

    def create(self, user_id: UUID, application_id: UUID, payload: ApplicationPackageCreate) -> ApplicationPackage:
        profile, application = self._owned_application(user_id, application_id)
        job = self.db.get(Job, application.job_id)
        if job is None:
            raise DomainError("JOB_NOT_FOUND", "Application job not found", 404)

        evidence_ids = set()
        for item in payload.items:
            for reference in item.evidence_refs:
                try:
                    evidence_ids.add(UUID(reference))
                except (ValueError, TypeError) as exc:
                    raise DomainError(
                        "INVALID_EVIDENCE_REFERENCE",
                        "Evidence references must be canonical evidence UUIDs.",
                        422,
                    ) from exc
        if evidence_ids:
            owned_ids = set(self.db.scalars(
                select(Evidence.id).where(
                    Evidence.candidate_profile_id == profile.id,
                    Evidence.id.in_(evidence_ids),
                )
            ).all())
            if owned_ids != evidence_ids:
                raise DomainError(
                    "EVIDENCE_NOT_FOUND",
                    "One or more evidence references are unavailable for this candidate.",
                    422,
                )

        for item in payload.items:
            if item.ai_execution_id is not None:
                execution = self.db.scalar(select(AIExecution).where(
                    AIExecution.id == item.ai_execution_id,
                    AIExecution.user_id == user_id,
                    AIExecution.status == "SUCCEEDED",
                ))
                if execution is None:
                    raise DomainError(
                        "AI_EXECUTION_NOT_FOUND",
                        "AI execution must belong to this candidate and have succeeded.",
                        422,
                    )

        current_version = self.db.scalar(
            select(func.max(ApplicationPackage.version_number)).where(
                ApplicationPackage.application_id == application.id
            )
        ) or 0
        package = ApplicationPackage(
            application_id=application.id,
            version_number=current_version + 1,
            status="DRAFT",
            source_snapshot=self._source_snapshot(profile, application, job),
            created_by_user_id=user_id,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(package)
        self.db.flush()
        for item in payload.items:
            self.db.add(ApplicationPackageItem(
                package_id=package.id,
                artifact_type=item.artifact_type,
                content=item.content,
                evidence_refs=item.evidence_refs,
                confidence=item.confidence,
                unsupported_claims=item.unsupported_claims,
                ai_execution_id=item.ai_execution_id,
                content_hash=self._hash_content(item.content),
                created_at=datetime.now(timezone.utc),
            ))
        self.db.commit()
        self.db.refresh(package)
        return package

    def list_for_application(self, user_id: UUID, application_id: UUID) -> list[tuple[ApplicationPackage, list[ApplicationPackageItem]]]:
        _, application = self._owned_application(user_id, application_id)
        packages = list(self.db.scalars(
            select(ApplicationPackage).where(
                ApplicationPackage.application_id == application.id
            ).order_by(ApplicationPackage.version_number.desc())
        ).all())
        return [(package, list(self.db.scalars(
            select(ApplicationPackageItem).where(
                ApplicationPackageItem.package_id == package.id
            ).order_by(ApplicationPackageItem.artifact_type)
        ).all())) for package in packages]

    def get(self, user_id: UUID, application_id: UUID, package_id: UUID) -> tuple[ApplicationPackage, list[ApplicationPackageItem]]:
        _, application = self._owned_application(user_id, application_id)
        package = self.db.scalar(select(ApplicationPackage).where(
            ApplicationPackage.id == package_id,
            ApplicationPackage.application_id == application.id,
        ))
        if package is None:
            raise DomainError("APPLICATION_PACKAGE_NOT_FOUND", "Application package not found", 404)
        items = list(self.db.scalars(select(ApplicationPackageItem).where(
            ApplicationPackageItem.package_id == package.id
        ).order_by(ApplicationPackageItem.artifact_type)).all())
        return package, items
