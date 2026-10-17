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
    AIExecution, Application, ApplicationPackage, ApplicationPackageItem, CandidateClaim,
    CandidateCVApproval, CandidateCVVersion, CandidateEvaluation, CandidateProfile,
    Evidence, EvidenceSource, Job, JobDescriptionVersion,
)


def hash_source_snapshot(snapshot: dict) -> str:
    """Hash the immutable source snapshot captured for a package version."""
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def artifact_metadata_hash(item: ApplicationPackageItem) -> str:
    """Hash package metadata that affects review readiness, not just rendered content."""
    metadata = {
        "evidence_refs": item.evidence_refs,
        "confidence": item.confidence,
        "unsupported_claims": item.unsupported_claims,
        "ai_execution_id": str(item.ai_execution_id) if item.ai_execution_id else None,
    }
    # Preserve hashes for legacy artifacts while including CV provenance when present.
    source_cv_version_id = getattr(item, "source_cv_version_id", None)
    if source_cv_version_id is not None:
        metadata["source_cv_version_id"] = str(source_cv_version_id)
    canonical = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ApplicationPackageService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _assert_source_snapshot_integrity(package: ApplicationPackage) -> None:
        try:
            current_hash = hash_source_snapshot(package.source_snapshot)
        except (TypeError, ValueError):
            current_hash = None
        if not isinstance(package.source_snapshot, dict) or current_hash != package.source_snapshot_hash:
            raise DomainError(
                "PACKAGE_SOURCE_SNAPSHOT_INTEGRITY_INVALID",
                "Package source provenance failed integrity verification.",
                409,
            )

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

    def _source_snapshot(
        self, profile: CandidateProfile, application: Application, job: Job,
        evidence_ids: set[UUID], cv_version_ids: set[UUID] | None = None,
    ) -> dict:
        evaluation = self.db.scalar(
            select(CandidateEvaluation).where(
                CandidateEvaluation.candidate_profile_id == profile.id,
                CandidateEvaluation.job_id == application.job_id,
            ).order_by(CandidateEvaluation.evaluated_at.desc()).limit(1)
        )
        jd = None
        version = (
            self.db.get(JobDescriptionVersion, evaluation.job_description_version_id)
            if evaluation and evaluation.job_description_version_id else None
        )
        if version is None:
            version = self.db.scalar(
                select(JobDescriptionVersion)
                .where(JobDescriptionVersion.job_id == application.job_id)
                .order_by(JobDescriptionVersion.version_number.desc())
                .limit(1)
            )
        if version is not None:
            jd = {
                "id": str(version.id),
                "version_number": version.version_number,
                "content": version.content,
                "content_hash": version.content_hash,
                "completeness_status": version.completeness_status,
            }
        approved_claims = list(self.db.scalars(select(CandidateClaim).where(
            CandidateClaim.candidate_profile_id == profile.id,
            CandidateClaim.status == "APPROVED",
        )).all())
        evidence_rows = (
            list(self.db.scalars(select(Evidence).where(
                Evidence.candidate_profile_id == profile.id,
                Evidence.id.in_(evidence_ids),
            ).order_by(Evidence.id)).all())
            if evidence_ids else []
        )
        evidence_snapshot = []
        for evidence in evidence_rows:
            source = self.db.get(EvidenceSource, evidence.evidence_source_id)
            evidence_snapshot.append({
                "id": str(evidence.id),
                "revision": evidence.revision,
                "status": evidence.status,
                "evidence_type": evidence.evidence_type,
                "content": evidence.content,
                "verified_at": evidence.verified_at.isoformat() if evidence.verified_at else None,
                "expires_at": evidence.expires_at.isoformat() if evidence.expires_at else None,
                "source": ({
                    "id": str(source.id),
                    "source_type": source.source_type,
                    "source_reference": source.source_reference,
                } if source else None),
            })
        cv_rows = (
            list(self.db.scalars(select(CandidateCVVersion).where(
                CandidateCVVersion.candidate_profile_id == profile.id,
                CandidateCVVersion.id.in_(cv_version_ids),
            ).order_by(CandidateCVVersion.id)).all())
            if cv_version_ids else []
        )
        cv_snapshot = []
        for cv_version in cv_rows:
            approval = self.db.scalar(select(CandidateCVApproval).where(
                CandidateCVApproval.candidate_profile_id == profile.id,
                CandidateCVApproval.cv_version_id == cv_version.id,
            ))
            cv_snapshot.append({
                "id": str(cv_version.id),
                "cv_key": cv_version.cv_key,
                "cv_type": cv_version.cv_type,
                "version_number": cv_version.version_number,
                "content_hash": cv_version.content_hash,
                "source_profile_revision": cv_version.source_profile_revision,
                "source_profile_hash": cv_version.source_profile_hash,
                "approved_at": approval.approved_at.isoformat() if approval else None,
            })
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
                "approved_claims": [{
                    "id": str(claim.id),
                    "revision": claim.revision,
                    "claim_type": claim.claim_type,
                    "claim_content": claim.claim_content,
                    "effective_from": claim.effective_from.isoformat() if claim.effective_from else None,
                    "effective_to": claim.effective_to.isoformat() if claim.effective_to else None,
                } for claim in approved_claims],
            },
            "evidence": evidence_snapshot,
            "cv_versions": cv_snapshot,
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

    def create(
        self, user_id: UUID, application_id: UUID, payload: ApplicationPackageCreate,
        *, expected_current_version: int | None = None,
    ) -> ApplicationPackage:
        profile, application = self._owned_application(user_id, application_id)
        job = self.db.get(Job, application.job_id)
        if job is None:
            raise DomainError("JOB_NOT_FOUND", "Application job not found", 404)

        evidence_ids = set()
        cv_version_ids = set()
        for item in payload.items:
            if item.source_cv_version_id is not None:
                if item.artifact_type != "tailored_cv":
                    raise DomainError(
                        "CV_SOURCE_ARTIFACT_TYPE_INVALID",
                        "A CV Studio source version can only be attached to a tailored CV artifact.",
                        422,
                    )
                cv_version_ids.add(item.source_cv_version_id)
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
            # Lock referenced evidence in a stable order until the package and its
            # source snapshot are committed. This prevents a concurrent evidence
            # edit/revocation from racing the provenance captured for this version.
            owned_ids = set(self.db.scalars(
                select(Evidence.id).where(
                    Evidence.candidate_profile_id == profile.id,
                    Evidence.id.in_(evidence_ids),
                ).order_by(Evidence.id).with_for_update()
            ).all())
            if owned_ids != evidence_ids:
                raise DomainError(
                    "EVIDENCE_NOT_FOUND",
                    "One or more evidence references are unavailable for this candidate.",
                    422,
                )

        if cv_version_ids:
            cv_rows = list(self.db.scalars(select(CandidateCVVersion).where(
                CandidateCVVersion.candidate_profile_id == profile.id,
                CandidateCVVersion.id.in_(cv_version_ids),
            ).order_by(CandidateCVVersion.id).with_for_update()).all())
            if {row.id for row in cv_rows} != cv_version_ids:
                raise DomainError(
                    "CV_VERSION_NOT_FOUND",
                    "One or more CV Studio versions are unavailable for this candidate.",
                    422,
                )
            approvals = set(self.db.scalars(select(CandidateCVApproval.cv_version_id).where(
                CandidateCVApproval.candidate_profile_id == profile.id,
                CandidateCVApproval.cv_version_id.in_(cv_version_ids),
            )).all())
            if approvals != cv_version_ids:
                raise DomainError(
                    "CV_VERSION_NOT_APPROVED",
                    "Only candidate-approved CV Studio versions can be used as package sources.",
                    409,
                )
            for cv_version in cv_rows:
                if (
                    self._hash_content(cv_version.content) != cv_version.content_hash
                    or hash_source_snapshot(cv_version.source_profile_snapshot) != cv_version.source_profile_hash
                ):
                    raise DomainError(
                        "CV_VERSION_INTEGRITY_INVALID",
                        "A CV Studio source version failed integrity verification.",
                        409,
                    )

        compatible_tasks = {
            "tailored_cv": {"cv_generation"},
            "cover_letter": {"cover_letter"},
            "application_q_and_a": {"application_questions", "suggested_answers"},
        }
        for item in payload.items:
            if item.ai_execution_id is not None:
                execution = self.db.scalar(select(AIExecution).where(
                    AIExecution.id == item.ai_execution_id,
                    AIExecution.user_id == user_id,
                    AIExecution.status == "SUCCEEDED",
                ))
                if execution is None or execution.task not in compatible_tasks[item.artifact_type]:
                    raise DomainError(
                        "AI_EXECUTION_NOT_FOUND",
                        "AI execution must belong to this candidate, have succeeded, and match the artifact type.",
                        422,
                    )

        current_version = self.db.scalar(
            select(func.max(ApplicationPackage.version_number)).where(
                ApplicationPackage.application_id == application.id
            )
        ) or 0
        if expected_current_version is not None and current_version != expected_current_version:
            raise DomainError(
                "PACKAGE_VERSION_CONFLICT",
                "The application package has a newer version. Reload before creating a revision.",
                409,
                {"current_version": current_version},
            )
        # Keep the original four-argument call shape for legacy tests/adapters when
        # no CV source is attached; only pass the new parameter when it is needed.
        if cv_version_ids:
            source_snapshot = self._source_snapshot(
                profile, application, job, evidence_ids, cv_version_ids
            )
        else:
            source_snapshot = self._source_snapshot(profile, application, job, evidence_ids)
        package = ApplicationPackage(
            application_id=application.id,
            version_number=current_version + 1,
            status="DRAFT",
            source_snapshot=source_snapshot,
            source_snapshot_hash=hash_source_snapshot(source_snapshot),
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
                source_cv_version_id=item.source_cv_version_id,
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
        for package in packages:
            self._assert_source_snapshot_integrity(package)
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
        self._assert_source_snapshot_integrity(package)
        items = list(self.db.scalars(select(ApplicationPackageItem).where(
            ApplicationPackageItem.package_id == package.id
        ).order_by(ApplicationPackageItem.artifact_type)).all())
        return package, items
