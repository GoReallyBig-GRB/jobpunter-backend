"""AI-assisted semantic audit of application-package claims against approved evidence."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.ai.schemas import GenerateRequest, GenerateOptions
from app.application.ai.service import execute_ai_task
from app.application.application_packages.service import (
    ApplicationPackageService, artifact_metadata_hash, assert_artifact_provenance_integrity,
    assert_package_evidence_provenance_integrity, hash_source_snapshot,
)

from app.infrastructure.db.models import (
    AIExecution, Application, ApplicationPackage, ApplicationPackageClaimValidation,
    ApplicationPackageItem, CandidateProfile, Evidence,
)


def _artifact_validation_snapshot(items) -> list[dict]:
    """Capture artifact identity and hashes before an AI call can commit the session."""
    return [{
        "id": str(item.id),
        "artifact_type": item.artifact_type,
        "content_hash": item.content_hash,
        "metadata_hash": artifact_metadata_hash(item),
    } for item in items]


def _evidence_validation_snapshot(evidence_rows) -> list[dict]:
    """Capture evidence revisions and content hashes before an AI call can commit."""
    return [{
        "id": str(row.id),
        "revision": row.revision,
        "content_hash": hashlib.sha256(json.dumps(
            row.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")).hexdigest(),
    } for row in sorted(evidence_rows, key=lambda item: str(item.id))]



class PackageClaimValidationService:
    def __init__(self, db: Session):
        self.db = db

    def get_latest(self, user_id: UUID, application_id: UUID, package_id: UUID) -> dict:
        """Retrieve the latest persisted claim report for this candidate-owned package version."""
        package, _items = ApplicationPackageService(self.db).get(
            user_id, application_id, package_id
        )
        record = self.db.scalar(select(ApplicationPackageClaimValidation).where(
            ApplicationPackageClaimValidation.package_id == package.id
        ).order_by(
            ApplicationPackageClaimValidation.created_at.desc(),
            ApplicationPackageClaimValidation.id.desc(),
        ).limit(1))
        if record is None:
            raise DomainError(
                "SEMANTIC_CLAIM_VALIDATION_NOT_FOUND",
                "No persisted claim-validation report exists for this package version.",
                404,
            )
        execution = self.db.get(AIExecution, record.ai_execution_id)
        if (
            execution is None
            or execution.user_id != user_id
            or execution.task != "claim_evidence_validation"
            or execution.status != "SUCCEEDED"
        ):
            raise DomainError(
                "SEMANTIC_CLAIM_VALIDATION_INVALID",
                "The persisted claim-validation execution could not be verified.",
                409,
            )
        if not isinstance(record.report, dict):
            raise DomainError(
                "SEMANTIC_CLAIM_REPORT_INTEGRITY_INVALID",
                "The persisted claim-validation report is malformed.",
                409,
            )
        canonical = json.dumps(
            record.report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != record.report_hash:
            raise DomainError(
                "SEMANTIC_CLAIM_REPORT_INTEGRITY_INVALID",
                "The persisted claim-validation report failed integrity verification.",
                409,
            )
        return {
            "validation_id": str(record.id),
            "package_id": str(package.id),
            "package_version_number": package.version_number,
            "ai_execution_id": str(execution.id),
            "report_hash": record.report_hash,
            "report": record.report,
        }

    async def validate(self, user_id: UUID, application_id: UUID, package_id: UUID) -> dict:
        profile = self.db.scalar(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
        if profile is None:
            raise DomainError("CANDIDATE_PROFILE_NOT_FOUND", "Candidate profile not found", 404)
        application = self.db.scalar(select(Application).where(
            Application.id == application_id,
            Application.candidate_profile_id == profile.id,
        ))
        if application is None:
            raise DomainError("APPLICATION_NOT_FOUND", "Application not found", 404)
        package = self.db.scalar(select(ApplicationPackage).where(
            ApplicationPackage.id == package_id,
            ApplicationPackage.application_id == application.id,
        ).with_for_update())
        if package is None:
            raise DomainError("APPLICATION_PACKAGE_NOT_FOUND", "Application package not found", 404)
        if package.status != "DRAFT":
            raise DomainError(
                "APPLICATION_PACKAGE_STATE_CONFLICT",
                "Only DRAFT package versions can be validated; create a new version to change the package.",
                409,
            )

        try:
            source_snapshot_hash = hash_source_snapshot(package.source_snapshot)
        except (TypeError, ValueError):
            source_snapshot_hash = None
        if (
            not isinstance(package.source_snapshot, dict)
            or source_snapshot_hash != package.source_snapshot_hash
        ):
            raise DomainError(
                "PACKAGE_SOURCE_SNAPSHOT_INTEGRITY_INVALID",
                "Package source provenance has changed; create a new package version before validation.",
                409,
            )

        items = list(self.db.scalars(select(ApplicationPackageItem).where(
            ApplicationPackageItem.package_id == package.id
        ).order_by(ApplicationPackageItem.artifact_type)).all())
        if not items:
            raise DomainError("APPLICATION_PACKAGE_EMPTY", "Package has no artifacts to validate", 409)
        assert_package_evidence_provenance_integrity(self.db, package, items)

        for item in items:
            try:
                canonical_content = json.dumps(
                    item.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
                current_content_hash = hashlib.sha256(canonical_content.encode("utf-8")).hexdigest()
            except (TypeError, ValueError):
                current_content_hash = None
            if current_content_hash != item.content_hash:
                raise DomainError(
                    "ARTIFACT_CONTENT_HASH_INVALID",
                    "Package artifact content has changed since it was created; create a new package version before validation.",
                    409,
                )

        # Report a direct content-hash failure before the broader snapshot check,
        # while keeping both integrity gates ahead of any AI validation call.
        assert_artifact_provenance_integrity(self.db, package, items)

        evidence_ids: set[UUID] = set()
        for item in items:
            for raw in item.evidence_refs or []:
                try:
                    evidence_ids.add(UUID(str(raw)))
                except (ValueError, TypeError, AttributeError):
                    raise DomainError(
                        "INVALID_EVIDENCE_REFERENCE",
                        "Package contains an invalid evidence reference; correct the package before validation.",
                        409,
                    )
            if item.artifact_type == "application_q_and_a":
                for answer in (item.content or {}).get("answers", []):
                    for raw in answer.get("evidence_basis", []) if isinstance(answer, dict) else []:
                        try:
                            evidence_ids.add(UUID(str(raw)))
                        except (ValueError, TypeError, AttributeError):
                            raise DomainError(
                                "INVALID_EVIDENCE_REFERENCE",
                                "Q&A contains an invalid evidence reference; correct the package before validation.",
                                409,
                            )

        evidence_rows = list(self.db.scalars(select(Evidence).where(
            Evidence.candidate_profile_id == profile.id,
            Evidence.id.in_(evidence_ids),
        )).all()) if evidence_ids else []
        evidence_by_id = {row.id: row for row in evidence_rows}
        if evidence_ids - set(evidence_by_id):
            raise DomainError(
                "EVIDENCE_NOT_FOUND",
                "Package references evidence that is not owned by this candidate.",
                409,
            )

        now = datetime.now(timezone.utc)
        evidence_payload = []
        for evidence_id in sorted(evidence_ids, key=str):
            evidence = evidence_by_id[evidence_id]
            expires_at = evidence.expires_at
            effective_from = evidence.effective_from
            effective_to = evidence.effective_to
            if expires_at and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if effective_from and effective_from.tzinfo is None:
                effective_from = effective_from.replace(tzinfo=timezone.utc)
            if effective_to and effective_to.tzinfo is None:
                effective_to = effective_to.replace(tzinfo=timezone.utc)
            if (
                evidence.status != "APPROVED"
                or (expires_at and expires_at <= now)
                or (effective_from and effective_from > now)
                or (effective_to and effective_to <= now)
            ):
                continue
            evidence_payload.append({
                "id": str(evidence.id),
                "type": evidence.evidence_type,
                "content": evidence.content,
                "verified_at": evidence.verified_at.isoformat() if evidence.verified_at else None,
            })

        artifact_payload = [{
            "artifact_type": item.artifact_type,
            "content": item.content,
            "declared_evidence_refs": item.evidence_refs or [],
            "declared_unsupported_claims": item.unsupported_claims or [],
        } for item in items]

        # execute_ai_task commits its execution record and expires ORM instances.
        # Snapshot the exact inputs before that commit so concurrent evidence changes
        # during generation cannot make a stale AI report appear current.
        artifact_snapshot = _artifact_validation_snapshot(items)
        evidence_snapshot = _evidence_validation_snapshot(evidence_rows)
        prompt = json.dumps({
            "artifacts": artifact_payload,
            "approved_candidate_evidence": evidence_payload,
        }, ensure_ascii=False, separators=(",", ":"))
        if len(prompt) > 78000:
            raise DomainError(
                "CLAIM_VALIDATION_INPUT_TOO_LARGE",
                "Package and evidence are too large for one validation pass; reduce the input or split validation.",
                413,
            )

        result = await execute_ai_task(
            GenerateRequest(
                task="claim_evidence_validation",
                prompt=prompt,
                options=GenerateOptions(jsonMode=True, maxTokens=4096),
            ),
            db=self.db,
            user_id=user_id,
        )
        report = result.parsed_data
        if not isinstance(report, dict) or not isinstance(report.get("checks"), list):
            raise HTTPException(status_code=502, detail={
                "code": "AI_INVALID_OUTPUT",
                "message": "Claim validation did not return a valid report.",
            })

        allowed_ids = {str(value) for value in evidence_ids}
        admissible_ids = {entry["id"] for entry in evidence_payload}
        evidence_ids_by_artifact = {
            item.artifact_type: {str(UUID(str(raw))) for raw in (item.evidence_refs or [])}
            for item in items
        }
        package_artifact_types = {item.artifact_type for item in items}
        audited_artifact_types = set(report.get("audited_artifact_types", []))
        if audited_artifact_types - package_artifact_types:
            raise HTTPException(status_code=502, detail={
                "code": "AI_INVALID_OUTPUT",
                "message": "Claim validation reported an artifact outside this package.",
            })
        missing_artifact_types = sorted(package_artifact_types - audited_artifact_types)
        if missing_artifact_types:
            report["unassessed_claims"] = list(report.get("unassessed_claims", [])) + [
                f"Artifact coverage was not confirmed for {artifact_type}."
                for artifact_type in missing_artifact_types
            ]
        for check in report["checks"]:
            if not isinstance(check, dict) or check.get("artifact_type") not in {
                "tailored_cv", "cover_letter", "application_q_and_a"
            }:
                raise HTTPException(status_code=502, detail={
                    "code": "AI_INVALID_OUTPUT",
                    "message": "Claim validation returned an invalid artifact reference.",
                })
            if check["artifact_type"] not in package_artifact_types:
                raise HTTPException(status_code=502, detail={
                    "code": "AI_INVALID_OUTPUT",
                    "message": "Claim validation returned a claim for an artifact outside this package.",
                })
            if any(str(ref) not in allowed_ids for ref in check.get("evidence_refs", [])):
                raise HTTPException(status_code=502, detail={
                    "code": "AI_INVALID_EVIDENCE_REFERENCE",
                    "message": "Claim validation referenced evidence outside this candidate package.",
                })
            artifact_evidence_ids = evidence_ids_by_artifact[check["artifact_type"]]
            if any(str(ref) not in artifact_evidence_ids for ref in check.get("evidence_refs", [])):
                raise HTTPException(status_code=502, detail={
                    "code": "AI_INVALID_EVIDENCE_REFERENCE",
                    "message": "Claim validation referenced evidence not linked to the audited artifact.",
                })
            if check.get("status") == "supported" and (
                not check.get("evidence_refs")
                or any(str(ref) not in admissible_ids for ref in check.get("evidence_refs", []))
            ):
                raise HTTPException(status_code=502, detail={
                    "code": "AI_INVALID_EVIDENCE_REFERENCE",
                    "message": "A supported claim must cite approved, current candidate evidence.",
                })

        report["coverage_complete"] = bool(report.get("coverage_complete")) and not report.get("unassessed_claims")
        report["candidate_review_required"] = (
            not report["coverage_complete"]
            or any(check.get("status") != "supported" or check.get("confidence") in {"low", "unknown"}
                   for check in report["checks"])
        )
        report["validated_at"] = datetime.now(timezone.utc).isoformat()
        report["evidence_snapshot_ids"] = sorted(allowed_ids)
        report["artifact_snapshot"] = artifact_snapshot
        report["evidence_snapshot"] = evidence_snapshot

        if result.execution_id is None:
            raise HTTPException(status_code=500, detail={
                "code": "AI_EXECUTION_REFERENCE_MISSING",
                "message": "Claim validation could not be linked to its execution record.",
            })
        execution = self.db.get(AIExecution, result.execution_id)
        if execution is None or execution.user_id != user_id or execution.task != "claim_evidence_validation" or execution.status != "SUCCEEDED":
            raise HTTPException(status_code=500, detail={
                "code": "AI_EXECUTION_REFERENCE_INVALID",
                "message": "Claim validation execution could not be verified.",
            })

        canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        record = ApplicationPackageClaimValidation(
            package_id=package.id,
            ai_execution_id=execution.id,
            report=report,
            report_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return {
            "validation_id": str(record.id),
            "package_id": str(package.id),
            "ai_execution_id": str(execution.id),
            "report_hash": record.report_hash,
            "report": record.report,
        }
