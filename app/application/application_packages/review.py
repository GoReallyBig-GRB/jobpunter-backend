"""Package readiness and candidate review gates, including semantic claim audit status.

The semantic audit is AI-assisted and evidence-grounded, not a formal proof of entailment.
"""
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.application.application_packages.service import artifact_metadata_hash, hash_source_snapshot
from app.infrastructure.db.models import (
    AIExecution, Application, ApplicationPackage, ApplicationPackageItem, ApplicationPackageClaimValidation, CandidateProfile, Evidence,
)

REQUIRED_ARTIFACTS = {"tailored_cv", "cover_letter", "application_q_and_a"}
READY_STATUS = "READY_FOR_REVIEW"
APPROVED_STATUS = "APPROVED"
REVISION_STATUS = "NEEDS_REVISION"


class ApplicationPackageReviewService:
    def __init__(self, db: Session):
        self.db = db

    def _owned_package(self, user_id: UUID, application_id: UUID, package_id: UUID):
        profile = self.db.scalar(
            select(CandidateProfile).where(CandidateProfile.user_id == user_id)
        )
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
        items = list(self.db.scalars(select(ApplicationPackageItem).where(
            ApplicationPackageItem.package_id == package.id
        ).order_by(ApplicationPackageItem.artifact_type)).all())
        return profile, package, items

    @staticmethod
    def _as_utc(value):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def readiness(
        self,
        profile_id: UUID,
        package_id: UUID,
        items: list[ApplicationPackageItem],
        user_id: UUID,
    ) -> list[dict]:
        blockers: list[dict] = []
        package = self.db.get(ApplicationPackage, package_id)
        try:
            snapshot_hash = hash_source_snapshot(package.source_snapshot) if package else None
        except (TypeError, ValueError):
            snapshot_hash = None
        if (
            package is None
            or not isinstance(package.source_snapshot, dict)
            or snapshot_hash != package.source_snapshot_hash
        ):
            blockers.append({"code": "PACKAGE_SOURCE_SNAPSHOT_INTEGRITY_INVALID"})
        by_type = {item.artifact_type: item for item in items}
        for missing in sorted(REQUIRED_ARTIFACTS - set(by_type)):
            blockers.append({"code": "ARTIFACT_MISSING", "artifact_type": missing})

        all_refs: set[UUID] = set()
        refs_by_item: dict[str, set[UUID]] = {}
        for item in items:
            try:
                canonical_content = json.dumps(
                    item.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
                current_content_hash = hashlib.sha256(canonical_content.encode("utf-8")).hexdigest()
            except (TypeError, ValueError):
                current_content_hash = None
            if current_content_hash != item.content_hash:
                blockers.append({
                    "code": "ARTIFACT_CONTENT_HASH_INVALID",
                    "artifact_type": item.artifact_type,
                })
            item_content = item.content
            if not isinstance(item_content, dict):
                blockers.append({
                    "code": "ARTIFACT_CONTENT_INVALID",
                    "artifact_type": item.artifact_type,
                })
                item_content = {}
            parsed: set[UUID] = set()
            for raw in item.evidence_refs or []:
                try:
                    parsed.add(UUID(raw))
                except (ValueError, TypeError, AttributeError):
                    blockers.append({
                        "code": "INVALID_EVIDENCE_REFERENCE",
                        "artifact_type": item.artifact_type,
                    })
            refs_by_item[item.artifact_type] = parsed
            all_refs.update(parsed)
            if not parsed:
                blockers.append({
                    "code": "EVIDENCE_REQUIRED",
                    "artifact_type": item.artifact_type,
                })
            if not isinstance(item.unsupported_claims, list):
                blockers.append({
                    "code": "ARTIFACT_UNSUPPORTED_CLAIMS_INVALID",
                    "artifact_type": item.artifact_type,
                })
            elif item.unsupported_claims:
                blockers.append({
                    "code": "UNSUPPORTED_CLAIMS_REMAIN",
                    "artifact_type": item.artifact_type,
                    "count": len(item.unsupported_claims),
                })
            if not isinstance(item.confidence, str) or item.confidence not in {
                "high", "medium", "low", "unknown"
            }:
                blockers.append({
                    "code": "ARTIFACT_CONFIDENCE_INVALID",
                    "artifact_type": item.artifact_type,
                })
            elif item.confidence in {"low", "unknown"}:
                blockers.append({
                    "code": "CONFIDENCE_REVIEW_REQUIRED",
                    "artifact_type": item.artifact_type,
                    "confidence": item.confidence,
                })
            if item.artifact_type == "application_q_and_a":
                answers = item_content.get("answers", [])
                if not isinstance(answers, list):
                    blockers.append({
                        "code": "APPLICATION_Q_AND_A_CONTENT_INVALID",
                        "artifact_type": item.artifact_type,
                    })
                    answers = []
                for index, answer in enumerate(answers):
                    if isinstance(answer, dict):
                        answer_unsupported_claims = answer.get("unsupported_claims")
                        if not isinstance(answer_unsupported_claims, list):
                            blockers.append({
                                "code": "ANSWER_UNSUPPORTED_CLAIMS_INVALID",
                                "artifact_type": item.artifact_type,
                                "answer_index": index,
                            })
                        elif answer_unsupported_claims:
                            blockers.append({
                                "code": "ANSWER_UNSUPPORTED_CLAIMS_REMAIN",
                                "artifact_type": item.artifact_type,
                                "answer_index": index,
                                "count": len(answer_unsupported_claims),
                            })
                    if isinstance(answer, dict):
                        answer_confidence = answer.get("confidence")
                        if not isinstance(answer_confidence, str) or answer_confidence not in {
                            "high", "medium", "low", "unknown"
                        }:
                            blockers.append({
                                "code": "ANSWER_CONFIDENCE_INVALID",
                                "artifact_type": item.artifact_type,
                                "answer_index": index,
                            })
                        elif answer_confidence in {"low", "unknown"}:
                            blockers.append({
                                "code": "ANSWER_CONFIDENCE_REVIEW_REQUIRED",
                                "artifact_type": item.artifact_type,
                                "answer_index": index,
                                "confidence": answer_confidence,
                            })
                    basis = answer.get("evidence_basis", []) if isinstance(answer, dict) else []
                    if not isinstance(basis, list) or not basis:
                        blockers.append({
                            "code": "ANSWER_EVIDENCE_REQUIRED",
                            "artifact_type": item.artifact_type,
                            "answer_index": index,
                        })
                        continue
                    for raw in basis:
                        try:
                            evidence_id = UUID(raw)
                        except (ValueError, TypeError, AttributeError):
                            blockers.append({
                                "code": "INVALID_ANSWER_EVIDENCE_REFERENCE",
                                "artifact_type": item.artifact_type,
                                "answer_index": index,
                            })
                            continue
                        if evidence_id not in parsed:
                            blockers.append({
                                "code": "ANSWER_EVIDENCE_NOT_LINKED_TO_ARTIFACT",
                                "artifact_type": item.artifact_type,
                                "answer_index": index,
                            })

        evidence_rows = list(self.db.scalars(select(Evidence).where(
            Evidence.candidate_profile_id == profile_id,
            Evidence.id.in_(all_refs),
        )).all()) if all_refs else []
        evidence_by_id = {row.id: row for row in evidence_rows}
        for evidence_id in sorted(all_refs - set(evidence_by_id), key=str):
            blockers.append({"code": "EVIDENCE_NOT_FOUND", "evidence_id": str(evidence_id)})

        now = datetime.now(timezone.utc)
        for item in items:
            for evidence_id in sorted(refs_by_item[item.artifact_type], key=str):
                evidence = evidence_by_id.get(evidence_id)
                if evidence is None:
                    continue
                if evidence.status != "APPROVED":
                    blockers.append({
                        "code": "EVIDENCE_NOT_APPROVED",
                        "artifact_type": item.artifact_type,
                        "evidence_id": str(evidence_id),
                    })
                expires_at = self._as_utc(evidence.expires_at)
                effective_from = self._as_utc(evidence.effective_from)
                effective_to = self._as_utc(evidence.effective_to)
                if expires_at and expires_at <= now:
                    blockers.append({
                        "code": "EVIDENCE_EXPIRED",
                        "artifact_type": item.artifact_type,
                        "evidence_id": str(evidence_id),
                    })
                if effective_from and effective_from > now:
                    blockers.append({
                        "code": "EVIDENCE_NOT_YET_EFFECTIVE",
                        "artifact_type": item.artifact_type,
                        "evidence_id": str(evidence_id),
                    })
                if effective_to and effective_to <= now:
                    blockers.append({
                        "code": "EVIDENCE_NO_LONGER_EFFECTIVE",
                        "artifact_type": item.artifact_type,
                        "evidence_id": str(evidence_id),
                    })
        validation = self.db.scalar(
            select(ApplicationPackageClaimValidation).where(
                ApplicationPackageClaimValidation.package_id == package_id
            ).order_by(ApplicationPackageClaimValidation.created_at.desc()).limit(1)
        )
        if validation is None:
            blockers.append({"code": "SEMANTIC_CLAIM_VALIDATION_REQUIRED"})
        else:
            execution = self.db.get(AIExecution, validation.ai_execution_id)
            if (
                execution is None
                or execution.user_id != user_id
                or execution.status != "SUCCEEDED"
                or execution.task != "claim_evidence_validation"
            ):
                blockers.append({"code": "SEMANTIC_CLAIM_VALIDATION_INVALID"})
            stored_report = validation.report
            if not isinstance(stored_report, dict):
                blockers.append({"code": "SEMANTIC_CLAIM_REPORT_INTEGRITY_INVALID"})
                report = {}
            else:
                report = stored_report
                canonical_report = json.dumps(
                    report, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
                expected_report_hash = hashlib.sha256(canonical_report.encode("utf-8")).hexdigest()
                if expected_report_hash != validation.report_hash:
                    blockers.append({"code": "SEMANTIC_CLAIM_REPORT_INTEGRITY_INVALID"})
            if report.get("coverage_complete") is not True or report.get("unassessed_claims"):
                blockers.append({"code": "SEMANTIC_CLAIM_COVERAGE_INCOMPLETE"})
            audited_artifact_types = report.get("audited_artifact_types")
            expected_artifact_types = {item.artifact_type for item in items}
            if (
                not isinstance(audited_artifact_types, list)
                or any(not isinstance(value, str) for value in audited_artifact_types)
                or len(audited_artifact_types) != len(set(audited_artifact_types))
                or set(audited_artifact_types) != expected_artifact_types
            ):
                blockers.append({"code": "SEMANTIC_CLAIM_ARTIFACT_COVERAGE_INCOMPLETE"})
            if report.get("candidate_review_required") is not False:
                blockers.append({"code": "SEMANTIC_CLAIM_REVIEW_REQUIRED"})
            checks = report.get("checks")
            if not isinstance(checks, list) or not checks:
                blockers.append({"code": "SEMANTIC_CLAIM_REPORT_EMPTY"})
            else:
                if any(not isinstance(check, dict) or check.get("status") != "supported" for check in checks):
                    blockers.append({"code": "SEMANTIC_CLAIM_UNSUPPORTED_OR_UNVERIFIABLE"})
                evidence_refs_by_artifact = {
                    artifact_type: {str(value) for value in evidence_ids}
                    for artifact_type, evidence_ids in refs_by_item.items()
                }
                for check in checks:
                    if not isinstance(check, dict):
                        continue
                    artifact_type = check.get("artifact_type")
                    if not isinstance(artifact_type, str) or artifact_type not in evidence_refs_by_artifact:
                        blockers.append({"code": "SEMANTIC_CLAIM_ARTIFACT_REFERENCE_INVALID"})
                        continue
                    claim_text = check.get("claim_text")
                    rationale = check.get("rationale")
                    confidence = check.get("confidence")
                    raw_claim_refs = check.get("evidence_refs")
                    if (
                        not isinstance(claim_text, str)
                        or not claim_text.strip()
                        or not isinstance(rationale, str)
                        or not rationale.strip()
                        or not isinstance(confidence, str)
                        or confidence not in {"high", "medium", "low", "unknown"}
                        or not isinstance(raw_claim_refs, list)
                        or any(not isinstance(ref, str) or not ref.strip() for ref in raw_claim_refs)
                    ):
                        blockers.append({"code": "SEMANTIC_CLAIM_CHECK_INVALID"})
                    claim_refs = check.get("evidence_refs")
                    if check.get("status") == "supported" and (
                        not isinstance(claim_refs, list) or not claim_refs
                    ):
                        blockers.append({"code": "SEMANTIC_CLAIM_EVIDENCE_REQUIRED"})
                        continue
                    if isinstance(claim_refs, list) and any(
                        str(value) not in evidence_refs_by_artifact[artifact_type]
                        for value in claim_refs
                    ):
                        blockers.append({"code": "SEMANTIC_CLAIM_EVIDENCE_NOT_LINKED_TO_ARTIFACT"})
            artifact_snapshot = report.get("artifact_snapshot")
            expected_artifacts = {
                (
                    str(item.id),
                    item.artifact_type,
                    item.content_hash,
                    artifact_metadata_hash(item),
                )
                for item in items
            }
            if not isinstance(artifact_snapshot, list):
                blockers.append({"code": "SEMANTIC_VALIDATION_ARTIFACT_CHANGED"})
                artifact_snapshot = []
            actual_artifact_entries = []
            for row in artifact_snapshot:
                if (
                    not isinstance(row, dict)
                    or not isinstance(row.get("id"), str)
                    or not isinstance(row.get("artifact_type"), str)
                    or not isinstance(row.get("content_hash"), str)
                    or not isinstance(row.get("metadata_hash"), str)
                ):
                    blockers.append({"code": "SEMANTIC_VALIDATION_ARTIFACT_CHANGED"})
                    continue
                actual_artifact_entries.append(
                    (row["id"], row["artifact_type"], row["content_hash"], row["metadata_hash"])
                )
            actual_artifacts = set(actual_artifact_entries)
            if (
                actual_artifacts != expected_artifacts
                or len(actual_artifact_entries) != len(expected_artifacts)
                or len(actual_artifact_entries) != len(actual_artifacts)
            ):
                blockers.append({"code": "SEMANTIC_VALIDATION_ARTIFACT_CHANGED"})
            evidence_snapshot = report.get("evidence_snapshot")
            expected_evidence_ids = {str(value) for value in all_refs}
            snapshot_ids = []
            if not isinstance(evidence_snapshot, list):
                blockers.append({"code": "SEMANTIC_VALIDATION_PROVENANCE_INVALID"})
                evidence_snapshot = []
            for snapshot in evidence_snapshot:
                if not isinstance(snapshot, dict):
                    blockers.append({"code": "SEMANTIC_VALIDATION_PROVENANCE_INVALID"})
                    continue
                try:
                    snapshot_id = UUID(str(snapshot.get("id", "")))
                    snapshot_ids.append(str(snapshot_id))
                except (ValueError, TypeError, AttributeError):
                    blockers.append({"code": "SEMANTIC_VALIDATION_PROVENANCE_INVALID"})
                    continue
                current_evidence = evidence_by_id.get(snapshot_id)
                if current_evidence is None:
                    blockers.append({"code": "SEMANTIC_VALIDATION_EVIDENCE_MISSING"})
                    continue
                current_hash = hashlib.sha256(json.dumps(
                    current_evidence.content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")).hexdigest()
                if current_evidence.revision != snapshot.get("revision") or current_hash != snapshot.get("content_hash"):
                    blockers.append({
                        "code": "SEMANTIC_VALIDATION_EVIDENCE_CHANGED",
                        "evidence_id": str(snapshot_id),
                    })
            if len(snapshot_ids) != len(set(snapshot_ids)) or set(snapshot_ids) != expected_evidence_ids:
                blockers.append({"code": "SEMANTIC_VALIDATION_EVIDENCE_COVERAGE_INCOMPLETE"})

        return blockers

    def prepare(self, user_id: UUID, application_id: UUID, package_id: UUID) -> dict:
        profile, package, items = self._owned_package(user_id, application_id, package_id)
        if package.status in {APPROVED_STATUS, REVISION_STATUS}:
            raise DomainError(
                "APPLICATION_PACKAGE_STATE_CONFLICT",
                f"Package in {package.status} status cannot be prepared for review.",
                409,
            )
        blockers = self.readiness(profile.id, package.id, items, user_id)
        if blockers:
            if package.status == READY_STATUS:
                package.status = "DRAFT"
                self.db.commit()
                self.db.refresh(package)
            return {"package": package, "blockers": blockers, "ready": False}
        package.status = READY_STATUS
        self.db.commit()
        self.db.refresh(package)
        return {"package": package, "blockers": [], "ready": True}

    def decide(self, user_id: UUID, application_id: UUID, package_id: UUID, decision: str) -> ApplicationPackage:
        profile, package, items = self._owned_package(user_id, application_id, package_id)
        if package.status != READY_STATUS:
            raise DomainError(
                "APPLICATION_PACKAGE_NOT_READY",
                "Only a package in READY_FOR_REVIEW status can receive a review decision.",
                409,
            )
        blockers = self.readiness(profile.id, package.id, items, user_id)
        if blockers:
            package.status = "DRAFT"
            self.db.commit()
            self.db.refresh(package)
            raise DomainError(
                "APPLICATION_PACKAGE_NOT_READY",
                "Package readiness checks no longer pass; resolve blockers and prepare it for review again.",
                409,
                {"blockers": blockers},
            )
        if decision == "approve":
            package.status = APPROVED_STATUS
        elif decision == "request_revision":
            package.status = REVISION_STATUS
        else:
            raise DomainError("INVALID_REVIEW_DECISION", "Decision must be approve or request_revision.", 422)
        self.db.commit()
        self.db.refresh(package)
        return package
