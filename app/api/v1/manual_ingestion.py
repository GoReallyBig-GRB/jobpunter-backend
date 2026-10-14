"""Authenticated manual job intake routed through the shared canonicalization pipeline."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.application.jobs.canonicalization import CanonicalizationService
from app.application.jobs.email_parser import parse_job_alert_email
from app.application.jobs.normalization import normalize_url
from app.infrastructure.db.models import AcquisitionRun, RawSourceRecord, Source, SourceConnector
from app.infrastructure.db.repositories.acquisition import SqlAlchemyAcquisitionRepository
from app.infrastructure.db.repositories.canonicalization import SqlAlchemyCanonicalizationRepository
from app.infrastructure.db.session import get_db
from app.ports.acquisition import RawAcquisition
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/candidate/jobs", tags=["candidate job ingestion"])
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_CHANNELS = {"MANUAL_URL", "MANUAL_JD", "MANUAL_EMAIL"}


class ManualJobIngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["MANUAL_URL", "MANUAL_JD", "MANUAL_EMAIL"]
    title: str | None = Field(default=None, max_length=500)
    company: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=40_000)
    source_url: str | None = Field(default=None, max_length=4_000)
    application_url: str | None = Field(default=None, max_length=4_000)
    external_job_id: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=500)
    employment_type: str | None = Field(default=None, max_length=64)
    application_fee_amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    application_fee_currency: str | None = Field(default=None, min_length=3, max_length=8)
    relocation_required: bool | None = None
    posting_date: datetime | None = None
    email_subject: str | None = Field(default=None, max_length=500)
    email_sender: str | None = Field(default=None, max_length=500)
    email_body: str | None = Field(default=None, max_length=40_000)

    @field_validator("title", "company", "description", "source_url", "application_url", "external_job_id", "location", "employment_type", "application_fee_currency", "email_subject", "email_sender", "email_body")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


def _manual_source(db: Session) -> tuple[Source, SourceConnector]:
    # One shared, user-submission source; the advisory lock prevents duplicate rows
    # when the first two submissions arrive concurrently.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": "jobpunter:manual-submission-source:v1"})
    source = db.scalar(select(Source).where(Source.source_type == "MANUAL_SUBMISSION").order_by(Source.created_at, Source.id).with_for_update())
    if source is None:
        now = datetime.now(timezone.utc)
        source = Source(
            name="User-provided job submissions",
            source_type="MANUAL_SUBMISSION",
            base_url=None,
            status="ACTIVE",
            permission_review_status="PENDING",
            operational_state="AVAILABLE",
            configuration={},
            created_at=now,
            updated_at=now,
        )
        db.add(source)
        db.flush()
    connector = db.scalar(select(SourceConnector).where(
        SourceConnector.source_id == source.id,
        SourceConnector.connector_type == "MANUAL_SUBMISSION",
        SourceConnector.connector_version == "1",
    ).with_for_update())
    if connector is None:
        now = datetime.now(timezone.utc)
        connector = SourceConnector(
            source_id=source.id,
            connector_type="MANUAL_SUBMISSION",
            connector_version="1",
            status="ACTIVE",
            configuration={},
            capabilities={"manual_url": True, "manual_jd": True, "manual_email": True},
            limits={"max_description_chars": 40000},
            created_at=now,
            updated_at=now,
        )
        db.add(connector)
        db.flush()
    db.commit()
    db.refresh(source)
    db.refresh(connector)
    return source, connector


def _payload_record(payload: ManualJobIngestionRequest) -> tuple[dict[str, Any], str | None, str | None]:
    data = payload.model_dump(mode="json", exclude_none=True)
    channel = payload.channel
    if channel == "MANUAL_EMAIL":
        if not payload.email_body or not payload.email_body.strip():
            raise HTTPException(status_code=422, detail={"code": "EMAIL_BODY_REQUIRED", "message": "Provide the job-alert or recruiter email body."})
        parsed = parse_job_alert_email(payload.email_subject or "", payload.email_body, payload.email_sender or "")
        jobs = parsed.get("extractedJobs") or []
        if parsed.get("parsingStatus") != "PROCESSED" or len(jobs) != 1:
            raise HTTPException(status_code=422, detail={
                "code": "EMAIL_REQUIRES_REVIEW",
                "message": "This email could not be safely reduced to exactly one job. Submit each job separately with its title, company, and description.",
                "parsing_status": parsed.get("parsingStatus"),
                "extracted_job_count": len(jobs),
            })
        job = jobs[0]
        data.update({
            "title": payload.title or job.get("title"),
            "company": payload.company or job.get("company"),
            "description": payload.description or job.get("description") or payload.email_body,
            "source_url": payload.source_url or job.get("jobUrl") or None,
            "application_url": payload.application_url or job.get("jobUrl") or None,
            "location": payload.location or job.get("location"),
            "detected_email_source": parsed.get("detectedSource"),
        })
    elif channel == "MANUAL_URL":
        if not payload.source_url:
            raise HTTPException(status_code=422, detail={"code": "SOURCE_URL_REQUIRED", "message": "Provide the job posting URL."})
        # URL-only retrieval is not silently claimed: until a permitted retriever is
        # configured, require user-provided details and preserve the URL as provenance.
        if not (payload.title and payload.company and payload.description):
            raise HTTPException(status_code=422, detail={
                "code": "MANUAL_DETAILS_REQUIRED",
                "message": "This endpoint does not fetch arbitrary URLs yet. Provide the title, company, and pasted job description alongside the URL.",
            })
        data["application_url"] = payload.application_url or payload.source_url
    else:
        if not (payload.title and payload.company and payload.description):
            raise HTTPException(status_code=422, detail={"code": "JOB_DETAILS_REQUIRED", "message": "Manual JD ingestion requires title, company, and description."})

    title = str(data.get("title") or "").strip()
    company = str(data.get("company") or "").strip()
    description = str(data.get("description") or "").strip()
    if not title or not company or not description:
        raise HTTPException(status_code=422, detail={"code": "JOB_DETAILS_REQUIRED", "message": "A single job must have a title, company, and description before it enters the canonical pipeline."})

    source_url = normalize_url(data.get("source_url") or data.get("application_url"))
    application_url = normalize_url(data.get("application_url") or data.get("source_url"))
    for field_name, url_value in (("source_url", source_url), ("application_url", application_url)):
        if url_value:
            try:
                parts = urlsplit(url_value)
                valid_url = parts.scheme.lower() in {"http", "https"} and bool(parts.hostname)
            except ValueError:
                valid_url = False
            if not valid_url:
                code = "INVALID_SOURCE_URL" if field_name == "source_url" else "INVALID_JOB_URL"
                raise HTTPException(status_code=422, detail={"code": code, "message": "Job URLs must be valid HTTP(S) URLs."})
    if channel == "MANUAL_URL" and not source_url:
        raise HTTPException(status_code=422, detail={"code": "SOURCE_URL_REQUIRED", "message": "Provide a valid HTTP(S) job URL."})

    raw = {
        "title": title,
        "company_name": company,
        "job_description": description,
        "description": description,
        "application_url": application_url,
        "url": application_url,
        "location": data.get("location"),
        "employment_type": data.get("employment_type"),
        "application_fee_amount": data.get("application_fee_amount"),
        "application_fee_currency": data.get("application_fee_currency"),
        "relocation_required": data.get("relocation_required"),
        "external_job_id": data.get("external_job_id"),
        "posting_date": data.get("posting_date"),
        "discovery_method": channel,
        "email_subject": data.get("email_subject"),
        "email_sender": data.get("email_sender"),
        "detected_email_source": data.get("detected_email_source"),
    }
    return raw, source_url, data.get("external_job_id")


@router.post("/manual-ingestion", response_model=dict, status_code=201)
def ingest_manual_job(
    payload: ManualJobIngestionRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Accept one user-provided job and route it through raw persistence and canonical resolution."""
    if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise HTTPException(status_code=422, detail="Idempotency-Key must be a safe 1-200 character token.")
    raw_content, source_url, external_job_id = _payload_record(payload)
    raw_content["submitted_source_url"] = source_url
    encoded = json.dumps(raw_content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    content_hash = hashlib.sha256(encoded).hexdigest()
    source, connector = _manual_source(db)
    repo = SqlAlchemyAcquisitionRepository(db)
    run_key = f"manual:{principal.user_id}:{idempotency_key}"
    if len(run_key) > 255:
        run_key = f"manual:{hashlib.sha256(run_key.encode()).hexdigest()}"
    existing_run = repo.get_run(source.id, run_key)
    if existing_run is not None:
        existing_raw = db.scalar(select(RawSourceRecord).where(RawSourceRecord.acquisition_run_id == existing_run.id))
        if existing_raw is None:
            raise HTTPException(status_code=409, detail={"code": "INGESTION_IN_PROGRESS", "message": "This idempotency key is already being processed. Retry shortly."})
        if existing_raw.content_hash != content_hash:
            raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED", "message": "Use a new Idempotency-Key when the submitted job content changes."})
        canonical = CanonicalizationService(SqlAlchemyCanonicalizationRepository(db)).process(existing_raw.id)
        response.status_code = 200
        return {"data": {"acquisition_run_id": str(existing_run.id), "acquisition_status": existing_run.status, "canonicalization": canonical, "idempotent_replay": True}}

    run, created = repo.create_run(source.id, connector.id, run_key)
    if not created:
        raise HTTPException(status_code=409, detail={"code": "INGESTION_IN_PROGRESS", "message": "This idempotency key is already being processed. Retry shortly."})
    observed_at = datetime.now(timezone.utc)
    item = RawAcquisition(
        source_id=source.id,
        external_record_id=external_job_id,
        source_url=source_url,
        raw_content=raw_content,
        content_hash=content_hash,
        observed_at=observed_at,
    )
    try:
        repo.persist_raw_if_new(run.id, item)
        deduplication_key = f"id:{external_job_id}" if external_job_id is not None else f"hash:{content_hash}"
        raw_record = db.scalar(select(RawSourceRecord).where(
            RawSourceRecord.source_id == source.id,
            RawSourceRecord.deduplication_key == deduplication_key,
            RawSourceRecord.content_hash == content_hash,
        ))
        if raw_record is None:
            raise RuntimeError("Persisted manual source record could not be retrieved.")
        canonical = CanonicalizationService(SqlAlchemyCanonicalizationRepository(db)).process(raw_record.id)
        repo.complete_run(run.id, 1, 1 if canonical.get("replayed") is False else 0)
        return {"data": {"acquisition_run_id": str(run.id), "acquisition_status": "SUCCEEDED", "canonicalization": canonical, "idempotent_replay": False}}
    except HTTPException:
        raise
    except Exception as exc:
        repo.fail_run(run.id, error_summary=f"manual_ingestion_error:{type(exc).__name__}")
        raise HTTPException(status_code=500, detail={"code": "MANUAL_INGESTION_FAILED", "message": "The job could not be processed. The failed acquisition is recorded; retry with a new Idempotency-Key after correcting the input."}) from exc
