"""Candidate-controlled, append-only Master CV and CV variant lifecycle."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import DomainError
from app.infrastructure.db.models import (
    CandidateCVApproval, CandidateCVVersion, CandidateProfile,
)

MAX_CV_CONTENT_BYTES = 1_000_000
CV_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _canonical_hash(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validated_object(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise DomainError("CV_CONTENT_INVALID", f"{label} must be a JSON object.", 422)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise DomainError("CV_CONTENT_INVALID", f"{label} must contain JSON-compatible values.", 422) from exc
    if len(encoded.encode("utf-8")) > MAX_CV_CONTENT_BYTES:
        raise DomainError("CV_CONTENT_TOO_LARGE", "CV content exceeds the 1 MB limit.", 413)
    if not value:
        raise DomainError("CV_CONTENT_EMPTY", "CV content cannot be empty.", 422)
    return copy.deepcopy(value)


class CVStudioService:
    def __init__(self, db: Session):
        self.db = db

    def _owned_profile(self, user_id: UUID, *, lock: bool = False) -> CandidateProfile:
        query = select(CandidateProfile).where(CandidateProfile.user_id == user_id)
        if lock:
            query = query.with_for_update()
        profile = self.db.scalar(query)
        if profile is None:
            raise DomainError("CANDIDATE_PROFILE_NOT_FOUND", "Candidate profile not found.", 404)
        return profile

    def _latest(self, profile_id: UUID, cv_key: str, *, lock: bool = False) -> CandidateCVVersion | None:
        query = (
            select(CandidateCVVersion)
            .where(
                CandidateCVVersion.candidate_profile_id == profile_id,
                CandidateCVVersion.cv_key == cv_key,
            )
            .order_by(CandidateCVVersion.version_number.desc())
            .limit(1)
        )
        if lock:
            query = query.with_for_update()
        return self.db.scalar(query)

    def _version(self, user_id: UUID, cv_key: str, version_number: int) -> CandidateCVVersion:
        profile = self._owned_profile(user_id)
        version = self.db.scalar(select(CandidateCVVersion).where(
            CandidateCVVersion.candidate_profile_id == profile.id,
            CandidateCVVersion.cv_key == cv_key,
            CandidateCVVersion.version_number == version_number,
        ))
        if version is None:
            raise DomainError("CV_VERSION_NOT_FOUND", "CV version not found.", 404)
        return version

    def _create_version(
        self, *, profile: CandidateProfile, user_id: UUID, cv_key: str, cv_type: str,
        title: str, version_number: int, content: dict, source_profile_revision: int,
        source_profile_snapshot: dict, source_master_version_id: UUID | None,
    ) -> CandidateCVVersion:
        safe_content = _validated_object(content, "CV content")
        safe_snapshot = _validated_object(source_profile_snapshot, "Source profile snapshot") if source_profile_snapshot else {}
        safe_title = title.strip()
        if not safe_title:
            raise DomainError("CV_TITLE_INVALID", "CV title cannot be empty.", 422)
        row = CandidateCVVersion(
            candidate_profile_id=profile.id,
            cv_key=cv_key,
            cv_type=cv_type,
            title=safe_title,
            version_number=version_number,
            content=safe_content,
            content_hash=_canonical_hash(safe_content),
            source_profile_revision=source_profile_revision,
            source_profile_snapshot=safe_snapshot,
            source_profile_hash=_canonical_hash(safe_snapshot),
            source_master_version_id=source_master_version_id,
            created_by_user_id=user_id,
        )
        self.db.add(row)
        return row

    def regenerate_master(self, user_id: UUID, expected_profile_revision: int) -> CandidateCVVersion:
        profile = self._owned_profile(user_id, lock=True)
        if profile.revision != expected_profile_revision:
            raise DomainError(
                "REVISION_CONFLICT", "Candidate profile has changed.", 409,
                {"current_revision": profile.revision},
            )
        snapshot = copy.deepcopy(profile.profile_data or {})
        if not snapshot:
            raise DomainError(
                "MASTER_PROFILE_EMPTY", "Add factual profile information before generating a Master CV.", 409,
            )
        latest = self._latest(profile.id, "master", lock=True)
        identity = snapshot.get("identity") or {}
        professional = snapshot.get("professional_identity") or {}
        candidate_name = identity.get("full_name")
        current_role = professional.get("current_role")
        title = (
            candidate_name.strip() if isinstance(candidate_name, str) and candidate_name.strip()
            else current_role.strip() if isinstance(current_role, str) and current_role.strip()
            else "Master CV"
        )
        version = self._create_version(
            profile=profile, user_id=user_id, cv_key="master", cv_type="MASTER",
            title=str(title)[:200], version_number=(latest.version_number + 1 if latest else 1),
            content=snapshot, source_profile_revision=profile.revision,
            source_profile_snapshot=snapshot, source_master_version_id=None,
        )
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise DomainError(
                "CV_VERSION_CONFLICT", "A concurrent CV update was saved. Reload and try again.", 409,
            ) from exc
        self.db.refresh(version)
        return version

    def create_variant(
        self, user_id: UUID, cv_key: str, title: str, source_master_version_id: UUID,
        content: dict | None = None,
    ) -> CandidateCVVersion:
        if not CV_KEY_PATTERN.fullmatch(cv_key) or cv_key == "master":
            raise DomainError("CV_KEY_INVALID", "Use a lowercase, URL-safe key other than 'master'.", 422)
        profile = self._owned_profile(user_id, lock=True)
        master = self.db.scalar(select(CandidateCVVersion).where(
            CandidateCVVersion.id == source_master_version_id,
            CandidateCVVersion.candidate_profile_id == profile.id,
            CandidateCVVersion.cv_type == "MASTER",
        ))
        if master is None:
            raise DomainError("MASTER_CV_VERSION_NOT_FOUND", "The selected Master CV version was not found.", 404)
        if self._latest(profile.id, cv_key, lock=True) is not None:
            raise DomainError("CV_KEY_ALREADY_EXISTS", "A CV with this key already exists.", 409)
        variant_content = copy.deepcopy(content if content is not None else master.content)
        row = self._create_version(
            profile=profile, user_id=user_id, cv_key=cv_key, cv_type="VARIANT",
            title=title, version_number=1, content=variant_content,
            source_profile_revision=master.source_profile_revision,
            source_profile_snapshot=master.source_profile_snapshot,
            source_master_version_id=master.id,
        )
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise DomainError("CV_VERSION_CONFLICT", "A concurrent CV update was saved. Reload and try again.", 409) from exc
        self.db.refresh(row)
        return row

    def save_version(
        self, user_id: UUID, cv_key: str, expected_latest_version: int,
        content: dict, title: str | None = None,
    ) -> CandidateCVVersion:
        if not CV_KEY_PATTERN.fullmatch(cv_key):
            raise DomainError("CV_KEY_INVALID", "CV key format is invalid.", 422)
        profile = self._owned_profile(user_id, lock=True)
        latest = self._latest(profile.id, cv_key, lock=True)
        if latest is None:
            raise DomainError("CV_NOT_FOUND", "CV not found.", 404)
        if latest.version_number != expected_latest_version:
            raise DomainError(
                "REVISION_CONFLICT", "CV has changed.", 409,
                {"current_version": latest.version_number},
            )
        row = self._create_version(
            profile=profile, user_id=user_id, cv_key=cv_key, cv_type=latest.cv_type,
            title=title or latest.title, version_number=latest.version_number + 1,
            content=content, source_profile_revision=latest.source_profile_revision,
            source_profile_snapshot=latest.source_profile_snapshot,
            source_master_version_id=latest.source_master_version_id,
        )
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise DomainError("CV_VERSION_CONFLICT", "A concurrent CV update was saved. Reload and try again.", 409) from exc
        self.db.refresh(row)
        return row

    def approve_version(
        self, user_id: UUID, cv_key: str, version_number: int, notes: str | None = None,
    ) -> CandidateCVApproval:
        # Serialize approval against every write for this candidate. This prevents a
        # save from creating a newer version between the latest-version check and approval.
        profile = self._owned_profile(user_id, lock=True)
        version = self.db.scalar(select(CandidateCVVersion).where(
            CandidateCVVersion.candidate_profile_id == profile.id,
            CandidateCVVersion.cv_key == cv_key,
            CandidateCVVersion.version_number == version_number,
        ))
        if version is None:
            raise DomainError("CV_VERSION_NOT_FOUND", "CV version not found.", 404)
        existing = self.db.scalar(select(CandidateCVApproval).where(
            CandidateCVApproval.cv_version_id == version.id
        ))
        # Approval retries remain idempotent even after a later version is saved.
        if existing is not None:
            return existing
        latest = self._latest(version.candidate_profile_id, cv_key)
        if latest is None or latest.version_number != version_number:
            raise DomainError("CV_VERSION_NOT_CURRENT", "Only the latest CV version can be approved.", 409)
        if not version.content:
            raise DomainError("CV_CONTENT_EMPTY", "An empty CV cannot be approved.", 422)
        approval = CandidateCVApproval(
            candidate_profile_id=version.candidate_profile_id,
            cv_version_id=version.id,
            approved_by_user_id=user_id,
            approved_at=datetime.now(timezone.utc),
            notes=notes.strip() if notes else None,
        )
        self.db.add(approval)
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            existing = self.db.scalar(select(CandidateCVApproval).where(
                CandidateCVApproval.cv_version_id == version.id
            ))
            if existing is not None:
                return existing
            raise DomainError("CV_APPROVAL_CONFLICT", "CV approval conflicted with another request.", 409) from exc
        self.db.refresh(approval)
        return approval

    def list_cvs(self, user_id: UUID) -> list[dict]:
        profile = self._owned_profile(user_id)
        rows = list(self.db.scalars(select(CandidateCVVersion).where(
            CandidateCVVersion.candidate_profile_id == profile.id
        ).order_by(CandidateCVVersion.cv_key, CandidateCVVersion.version_number.desc())).all())
        latest_by_key: dict[str, CandidateCVVersion] = {}
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.cv_key] = counts.get(row.cv_key, 0) + 1
            latest_by_key.setdefault(row.cv_key, row)
        result = []
        for key, row in latest_by_key.items():
            approval = self.db.scalar(select(CandidateCVApproval).where(
                CandidateCVApproval.cv_version_id == row.id
            ))
            result.append(self._serialize(row, approval, version_count=counts[key], include_content=False))
        return result

    def get_cv(self, user_id: UUID, cv_key: str) -> dict:
        profile = self._owned_profile(user_id)
        rows = list(self.db.scalars(select(CandidateCVVersion).where(
            CandidateCVVersion.candidate_profile_id == profile.id,
            CandidateCVVersion.cv_key == cv_key,
        ).order_by(CandidateCVVersion.version_number.desc())).all())
        if not rows:
            raise DomainError("CV_NOT_FOUND", "CV not found.", 404)
        history = []
        for row in rows:
            approval = self.db.scalar(select(CandidateCVApproval).where(
                CandidateCVApproval.cv_version_id == row.id
            ))
            history.append(self._serialize(row, approval, include_content=True))
        return {"latest": history[0], "versions": history}

    @staticmethod
    def _serialize(
        row: CandidateCVVersion, approval: CandidateCVApproval | None,
        *, version_count: int | None = None, include_content: bool = True,
    ) -> dict:
        result = {
            "id": str(row.id), "cv_key": row.cv_key, "cv_type": row.cv_type,
            "title": row.title, "version_number": row.version_number,
            "content_hash": row.content_hash,
            "source_profile_revision": row.source_profile_revision,
            "source_profile_hash": row.source_profile_hash,
            "source_master_version_id": str(row.source_master_version_id) if row.source_master_version_id else None,
            "created_at": row.created_at.isoformat(),
            "approved": approval is not None,
            "approved_at": approval.approved_at.isoformat() if approval else None,
        }
        if version_count is not None:
            result["version_count"] = version_count
        if include_content:
            result["content"] = row.content
            result["source_profile_snapshot"] = row.source_profile_snapshot
        return result
