"""Extract explicit requirements into a human-review queue."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import time
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import UUID

from fastapi import HTTPException

from app.application.ai.schemas import GenerateRequest
from app.application.ai.service import execute_ai_task

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.infrastructure.ai.job_requirement_extractor import (
    EXTRACTION_VERSION,
    ExtractedRequirement,
    _safe_description_for_provider,
    _normalized_quote,
)
from app.infrastructure.db.models import Job, JobDescriptionVersion, JobRequirement
from app.infrastructure.db.session import SessionLocal

logger = logging.getLogger("jobpunter.job_requirement_extraction_worker")
STALE_CLAIM_AFTER = timedelta(minutes=5)
MAX_RETRY_DELAY = timedelta(hours=6)


def _same_timestamp(left: datetime | None, right: datetime) -> bool:
    if left is None:
        return False
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    return left == right


class JobRequirementExtractionWorker:
    def __init__(
        self,
        session_factory=SessionLocal,
        extractor: Callable[..., list[ExtractedRequirement]] | None = None,
    ):
        self.session_factory = session_factory
        # Tests may inject a deterministic extractor. Production uses the canonical
        # AI task service so provider routing, published config and AIExecution
        # provenance cannot be bypassed by this background worker.
        self.extractor = extractor

    def _extract_with_governance(
        self, *, title: str, company: str | None, description: str, version_id: UUID
    ) -> list[ExtractedRequirement]:
        safe_description = _safe_description_for_provider(description)
        payload = {
            "job_title": title,
            "employer": company,
            "job_description": safe_description,
        }
        request = GenerateRequest(
            task="job_requirement_extraction",
            prompt=json.dumps(payload, ensure_ascii=False),
            options={"jsonMode": True, "maxTokens": 4096},
        )
        with self.session_factory() as db:
            response = asyncio.run(execute_ai_task(
                request,
                db=db,
                user_id=None,
                request_id=f"job-requirement-extraction:{version_id}",
            ))

        parsed = response.parsed_data
        items = parsed.get("requirements") if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            raise ValueError("Governed requirement extraction returned no valid requirements array.")

        extracted: list[ExtractedRequirement] = []
        normalized_description = _normalized_quote(safe_description)
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Governed requirement extraction returned an invalid item.")
            excerpt = item.get("source_excerpt")
            if not isinstance(excerpt, str) or _normalized_quote(excerpt) not in normalized_description:
                raise ValueError("Governed requirement extraction returned an ungrounded source excerpt.")
            extracted.append(ExtractedRequirement(
                requirement_type=item["requirement_type"],
                description=item["description"],
                source_excerpt=excerpt,
                importance=item["importance"],
                hard_constraint=item["hard_constraint"],
                confidence=float(item["confidence"]),
            ))
        return extracted

    def _claim_one(self, db: Session, now: datetime):
        stale_before = now - STALE_CLAIM_AFTER
        statement = (
            select(JobDescriptionVersion)
            .where(
                JobDescriptionVersion.completeness_status == "USABLE",
                or_(
                    JobDescriptionVersion.requirement_extraction_status == "PENDING",
                    (
                        (JobDescriptionVersion.requirement_extraction_status == "FAILED")
                        & (
                            JobDescriptionVersion.requirement_extraction_retry_at.is_(None)
                            | (JobDescriptionVersion.requirement_extraction_retry_at <= now)
                        )
                    ),
                    (
                        (JobDescriptionVersion.requirement_extraction_status == "IN_PROGRESS")
                        & (JobDescriptionVersion.requirement_extraction_started_at <= stale_before)
                    ),
                ),
            )
            .order_by(JobDescriptionVersion.retrieved_at, JobDescriptionVersion.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return db.scalar(statement)

    def run_once(self, limit: int = 10) -> dict[str, int]:
        if limit < 1:
            raise ValueError("limit must be positive")
        outcome = {
            "claimed": 0, "needs_review": 0, "no_requirements": 0,
            "failed": 0, "skipped": 0, "disabled": 0,
        }
        if self.extractor is None and not any(
            os.getenv(key, "").strip()
            for key in ("JOBPUNTER_GOOGLE_AI_API_KEY", "JOBPUNTER_OPENAI_API_KEY")
        ):
            outcome["disabled"] = 1
            return outcome

        for _ in range(limit):
            with self.session_factory() as db:
                now = datetime.now(timezone.utc)
                description_version = self._claim_one(db, now)
                if description_version is None:
                    db.rollback()
                    break
                version_id = description_version.id
                claim_time = now
                description_version.requirement_extraction_status = "IN_PROGRESS"
                description_version.requirement_extraction_attempts += 1
                attempts = description_version.requirement_extraction_attempts
                description_version.requirement_extraction_started_at = claim_time
                description_version.requirement_extraction_retry_at = None
                description_version.requirement_extraction_error = None
                job = db.get(Job, description_version.job_id)
                if job is None:
                    description_version.requirement_extraction_status = "FAILED"
                    description_version.requirement_extraction_error = "JOB_NOT_FOUND"
                    description_version.requirement_extraction_started_at = None
                    db.commit()
                    outcome["claimed"] += 1
                    outcome["failed"] += 1
                    continue
                title = job.canonical_title
                company = job.company_name
                content = description_version.content
                db.commit()

            outcome["claimed"] += 1
            try:
                if self.extractor is not None:
                    extracted = self.extractor(title=title, company=company, description=content)
                else:
                    extracted = self._extract_with_governance(
                        title=title, company=company, description=content, version_id=version_id
                    )
            except Exception as exc:
                with self.session_factory() as db:
                    current = db.scalar(
                        select(JobDescriptionVersion)
                        .where(JobDescriptionVersion.id == version_id)
                        .with_for_update()
                    )
                    if current is not None and _same_timestamp(current.requirement_extraction_started_at, claim_time):
                        delay_seconds = min(60 * (2 ** min(max(attempts - 1, 0), 8)), int(MAX_RETRY_DELAY.total_seconds()))
                        current.requirement_extraction_status = "FAILED"
                        current.requirement_extraction_error = type(exc).__name__[:64]
                        current.requirement_extraction_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
                        current.requirement_extraction_started_at = None
                        db.commit()
                logger.warning(
                    "job requirement extraction failed version=%s error=%s",
                    version_id, type(exc).__name__,
                )
                outcome["failed"] += 1
                continue

            with self.session_factory() as db:
                current = db.scalar(
                    select(JobDescriptionVersion)
                    .where(JobDescriptionVersion.id == version_id)
                    .with_for_update()
                )
                if current is None or not _same_timestamp(current.requirement_extraction_started_at, claim_time):
                    db.rollback()
                    outcome["skipped"] += 1
                    continue

                existing = db.scalars(
                    select(JobRequirement).where(
                        JobRequirement.job_description_version_id == current.id
                    )
                ).all()
                if existing:
                    current.requirement_extraction_status = "NEEDS_REVIEW"
                    current.requirement_extraction_version = EXTRACTION_VERSION
                    current.requirements_extracted_at = datetime.now(timezone.utc)
                    current.requirement_extraction_started_at = None
                    current.requirement_extraction_error = None
                    db.commit()
                    outcome["needs_review"] += 1
                    continue

                for item in extracted:
                    db.add(JobRequirement(
                        job_description_version_id=current.id,
                        requirement_type=item.requirement_type,
                        description=item.description,
                        importance=item.importance,
                        hard_constraint=item.hard_constraint,
                        # Extraction is never trusted as reviewer approval.
                        extraction_status="NEEDS_REVIEW",
                        source_excerpt=item.source_excerpt,
                        extraction_confidence=item.confidence,
                        extraction_version=EXTRACTION_VERSION,
                    ))
                current.requirement_extraction_status = "NEEDS_REVIEW" if extracted else "NO_REQUIREMENTS"
                current.requirement_extraction_version = EXTRACTION_VERSION
                current.requirements_extracted_at = datetime.now(timezone.utc)
                current.requirement_extraction_started_at = None
                current.requirement_extraction_retry_at = None
                current.requirement_extraction_error = None
                db.commit()
                if extracted:
                    outcome["needs_review"] += 1
                else:
                    outcome["no_requirements"] += 1
        return outcome


def run_worker(
    *,
    interval_seconds: float = 10.0,
    batch_size: int = 10,
    once: bool = False,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
    worker: JobRequirementExtractionWorker | None = None,
) -> int:
    if interval_seconds <= 0 or batch_size < 1:
        raise ValueError("interval_seconds and batch_size must be positive")
    consumer = worker or JobRequirementExtractionWorker()
    cycles = 0
    while not should_stop():
        outcome = consumer.run_once(batch_size)
        cycles += 1
        logger.info("job requirement extraction cycle=%s outcome=%s", cycles, outcome)
        if once:
            break
        if outcome["claimed"] == 0:
            sleep(interval_seconds)
    return cycles


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--interval-seconds", type=float, default=10.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stopped = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    run_worker(
        interval_seconds=args.interval_seconds,
        batch_size=args.batch_size,
        once=args.once,
        should_stop=lambda: stopped,
    )


if __name__ == "__main__":
    main()
