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
from app.application.jobs.manual_url_retrieval import JobUrlRetrievalError, retrieve_job_posting
from app.application.jobs.normalization import normalize_url
from app.infrastructure.db.models import AcquisitionRun, JobDiscoveryEvent, RawSourceRecord, Source, SourceConnector
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
    application_route_type: str | None = Field(default=None, max_length=32)
    posting_date: datetime | None = None
    email_subject: str | None = Field(default=None, max_length=500)
    email_sender: str | None = Field(default=None, max_length=500)
    email_message_id: str | None = Field(default=None, max_length=500)
    email_body: str | None = Field(default=None, max_length=40_000)

    @field_validator("title", "company", "description", "source_url", "application_url", "external_job_id", "location", "employment_type", "application_fee_currency", "application_route_type", "email_subject", "email_sender", "email_message_id", "email_body")
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
        if not (payload.title and payload.company and payload.description):
            try:
                fetched = retrieve_job_posting(payload.source_url)
            except JobUrlRetrievalError as exc:
                raise HTTPException(status_code=422, detail={
                    "code": "URL_RETRIEVAL_FAILED",
                    "message": "The posting could not be retrieved safely and completely. Paste the job title, company, and full description, then retry.",
                    "retrieval_status": "FAILED",
                    "failure_category": exc.category,
                    "pasted_text_fallback_available": True,
                }) from exc
            data["title"] = payload.title or fetched.get("title")
            data["company"] = payload.company or fetched.get("company")
            data["description"] = payload.description or fetched.get("description")
            data["location"] = payload.location or fetched.get("location")
            # The posting URL is source provenance, not necessarily an application
            # destination. Only use an explicitly supplied or extracted apply URL.
            data["application_url"] = payload.application_url or fetched.get("application_url")
            data["url_retrieval"] = {
                "status": "SUCCEEDED",
                "method": fetched.get("retrieval_method"),
                "final_url": fetched.get("retrieval_final_url"),
                "retrieved_at": fetched.get("retrieved_at"),
            }
        else:
            # Pasted details avoid an unnecessary fetch; the posting URL remains provenance.
            data["application_url"] = payload.application_url
            data["url_retrieval"] = {
                "status": "NOT_ATTEMPTED",
                "method": "PASTED_TEXT_FALLBACK",
            }
    else:
        if not (payload.title and payload.company and payload.description):
            raise HTTPException(status_code=422, detail={"code": "JOB_DETAILS_REQUIRED", "message": "Manual JD ingestion requires title, company, and description."})

    title = str(data.get("title") or "").strip()
    company = str(data.get("company") or "").strip()
    description = str(data.get("description") or "").strip()
    if not title or not company or not description:
        raise HTTPException(status_code=422, detail={"code": "JOB_DETAILS_REQUIRED", "message": "A single job must have a title, company, and description before it enters the canonical pipeline."})

    source_url = normalize_url(data.get("source_url") or data.get("application_url"))
    # A manual posting URL is provenance, not an application destination. Only an
    # explicit or extracted application URL may populate the canonical apply route.
    application_url = normalize_url(
        data.get("application_url")
        or (None if channel == "MANUAL_URL" else data.get("source_url"))
    )
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
        "application_route_type": data.get("application_route_type"),
        "external_job_id": data.get("external_job_id"),
        "posting_date": data.get("posting_date"),
        "discovery_method": channel,
        "email_subject": data.get("email_subject"),
        "email_sender": data.get("email_sender"),
        "email_message_id": data.get("email_message_id"),
        "detected_email_source": data.get("detected_email_source"),
        "url_retrieval": data.get("url_retrieval"),
    }
    return raw, source_url, data.get("external_job_id")



def _payload_records(
    payload: ManualJobIngestionRequest,
) -> tuple[list[tuple[dict[str, Any], str | None, str | None]], dict[str, Any] | None]:
    """Expand email digests into independently canonicalized jobs, retaining ambiguous chunks."""
    if payload.channel != "MANUAL_EMAIL":
        raw, source_url, external_job_id = _payload_record(payload)
        return [(raw, source_url, external_job_id)], None

    if not payload.email_body:
        raise HTTPException(status_code=422, detail={
            "code": "EMAIL_BODY_REQUIRED",
            "message": "Provide the job-alert or recruiter email body.",
        })
    parsed = parse_job_alert_email(
        payload.email_subject or "",
        payload.email_body,
        payload.email_sender or "",
        source_message_id=payload.email_message_id,
    )
    jobs = parsed.get("extractedJobs") or []
    review_chunks = list(parsed.get("reviewRequiredChunks") or [])
    if not jobs:
        raise HTTPException(status_code=422, detail={
            "code": "EMAIL_REQUIRES_REVIEW",
            "message": "No complete job could be extracted confidently. Review the email chunks and submit corrected job details.",
            "parsing_status": parsed.get("parsingStatus"),
            "review_required_chunks": review_chunks,
        })

    records: list[tuple[dict[str, Any], str | None, str | None]] = []
    records_by_identity: dict[str, dict[str, Any]] = {}
    message_identity = payload.email_message_id or parsed["sourceEmail"]["id"]
    for job in jobs:
        title = str(job.get("title") or "").strip()
        company = str(job.get("company") or "").strip()
        description = str(job.get("description") or "").strip()
        if not title or not company or not description:
            review_chunks.append({"content": job, "reason": "REQUIRED_JOB_FIELDS_MISSING"})
            continue

        job_url = job.get("jobUrl") or None
        stable_identity = hashlib.sha256(
            f"{message_identity}\x1f{title.casefold()}\x1f{company.casefold()}\x1f{normalize_url(job_url) or ''}".encode("utf-8")
        ).hexdigest()
        job_payload = ManualJobIngestionRequest(
            channel="MANUAL_JD",
            title=title,
            company=company,
            description=description,
            source_url=job_url,
            application_url=None,
            external_job_id=f"email-job:{stable_identity}",
            location=job.get("location"),
            email_subject=payload.email_subject,
            email_sender=payload.email_sender,
            email_message_id=message_identity,
        )
        raw, source_url, external_job_id = _payload_record(job_payload)
        # A digest link is source provenance, not proof of the application destination.
        raw["application_url"] = None
        raw["url"] = None
        raw["discovery_method"] = "MANUAL_EMAIL"
        raw["email_message_id"] = message_identity
        raw["email_message_fingerprint"] = parsed["sourceEmail"]["id"]
        raw["detected_email_source"] = parsed.get("detectedSource")

        if external_job_id is not None and external_job_id in records_by_identity:
            if records_by_identity[external_job_id] != raw:
                review_chunks.append({
                    "content": job,
                    "reason": "DUPLICATE_JOB_IDENTITY_CONFLICT",
                })
            continue
        if external_job_id is not None:
            records_by_identity[external_job_id] = raw
        records.append((raw, source_url, external_job_id))

    if not records:
        raise HTTPException(status_code=422, detail={
            "code": "EMAIL_REQUIRES_REVIEW",
            "message": "No complete job could be extracted confidently. Review the email chunks and submit corrected job details.",
            "parsing_status": parsed.get("parsingStatus"),
            "review_required_chunks": review_chunks,
        })
    return records, {
        "email_message_id": message_identity,
        "processed_job_count": len(records),
        "review_required_chunks": review_chunks,
        "review_required_count": len(review_chunks),
        "parsing_status": "PARTIALLY_PROCESSED" if review_chunks else "PROCESSED",
    }


@router.post("/manual-ingestion", response_model=dict, status_code=201)
def ingest_manual_job(
    payload: ManualJobIngestionRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Accept a job or email digest and route every complete job through canonicalization."""
    if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        raise HTTPException(status_code=422, detail="Idempotency-Key must be a safe 1-200 character token.")

    records, email_result = _payload_records(payload)
    prepared = []
    for raw_content, source_url, external_job_id in records:
        raw_content["submitted_source_url"] = source_url
        # Keep the source content's ownership in durable provenance, not in the shared
        # canonical Job fields. The owner value comes from the authenticated principal.
        raw_content["submitted_by_user_id"] = str(principal.user_id)
        encoded = json.dumps(raw_content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        prepared.append((
            raw_content,
            source_url,
            external_job_id,
            hashlib.sha256(encoded).hexdigest(),
        ))

    source, connector = _manual_source(db)
    repo = SqlAlchemyAcquisitionRepository(db)
    run_key = f"manual:{principal.user_id}:{idempotency_key}"
    if len(run_key) > 255:
        run_key = f"manual:{hashlib.sha256(run_key.encode()).hexdigest()}"
    expected_hashes = sorted(item[3] for item in prepared)
    existing_run = repo.get_run(source.id, run_key)
    if existing_run is not None:
        observations = db.scalars(
            select(JobDiscoveryEvent)
            .where(JobDiscoveryEvent.acquisition_run_id == existing_run.id)
            .order_by(JobDiscoveryEvent.created_at, JobDiscoveryEvent.id)
        ).all()
        existing_raws = [
            raw for observation in observations
            if (raw := db.get(RawSourceRecord, observation.raw_source_record_id)) is not None
        ]
        if not existing_raws:
            existing_raws = db.scalars(
                select(RawSourceRecord)
                .where(RawSourceRecord.acquisition_run_id == existing_run.id)
                .order_by(RawSourceRecord.created_at, RawSourceRecord.id)
            ).all()
        if not existing_raws:
            raise HTTPException(status_code=409, detail={
                "code": "INGESTION_IN_PROGRESS",
                "message": "This idempotency key is already being processed. Retry shortly.",
            })
        if sorted(record.content_hash for record in existing_raws) != expected_hashes:
            raise HTTPException(status_code=409, detail={
                "code": "IDEMPOTENCY_KEY_REUSED",
                "message": "Use a new Idempotency-Key when the submitted job content changes.",
            })
        canonicalizations = [
            CanonicalizationService(SqlAlchemyCanonicalizationRepository(db)).process(record.id)
            for record in existing_raws
        ]
        response.status_code = 200
        metrics = existing_run.metrics if isinstance(existing_run.metrics, dict) else {}
        result = {
            "acquisition_run_id": str(existing_run.id),
            "acquisition_status": existing_run.status,
            "canonicalization": canonicalizations[0],
            "canonicalizations": canonicalizations,
            "idempotent_replay": True,
            "acquired_count": int(metrics.get("acquired_count", 0)),
            "persisted_count": int(metrics.get("persisted_count", 0)),
            "duplicate_count": max(int(metrics.get("acquired_count", 0)) - int(metrics.get("persisted_count", 0)), 0),
            "observed_count": int(metrics.get("observed_count", 0)),
            "new_count": int(metrics.get("new_count", 0)),
            "changed_count": int(metrics.get("changed_count", 0)),
            "unchanged_count": int(metrics.get("unchanged_count", 0)),
        }
        if email_result:
            result.update(email_result)
        return {"data": result}

    run, created = repo.create_run(source.id, connector.id, run_key)
    if not created:
        raise HTTPException(status_code=409, detail={
            "code": "INGESTION_IN_PROGRESS",
            "message": "This idempotency key is already being processed. Retry shortly.",
        })

    canonicalizations = []
    persisted_count = 0
    try:
        for raw_content, source_url, external_job_id, content_hash in prepared:
            item = RawAcquisition(
                source_id=source.id,
                external_record_id=external_job_id,
                source_url=source_url,
                raw_content=raw_content,
                content_hash=content_hash,
                observed_at=datetime.now(timezone.utc),
            )
            persisted_new = repo.persist_raw_if_new(run.id, item)
            repo.record_observation(run.id, item, persisted_new=persisted_new)
            persisted_count += int(persisted_new)
            deduplication_key = repo._deduplication_key(item)
            raw_record = db.scalar(select(RawSourceRecord).where(
                RawSourceRecord.source_id == source.id,
                RawSourceRecord.deduplication_key == deduplication_key,
                RawSourceRecord.content_hash == content_hash,
            ))
            if raw_record is None:
                raise RuntimeError("Persisted manual source record could not be retrieved.")
            canonicalizations.append(
                CanonicalizationService(SqlAlchemyCanonicalizationRepository(db)).process(raw_record.id)
            )

        repo.complete_run(run.id, len(prepared), persisted_count)
        completed_run = repo.get_run(source.id, run_key)
        metrics = completed_run.metrics if completed_run and isinstance(completed_run.metrics, dict) else {}
        result = {
            "acquisition_run_id": str(run.id),
            "acquisition_status": "SUCCEEDED",
            "canonicalization": canonicalizations[0],
            "canonicalizations": canonicalizations,
            "idempotent_replay": False,
            "acquired_count": int(metrics.get("acquired_count", len(prepared))),
            "persisted_count": int(metrics.get("persisted_count", persisted_count)),
            "duplicate_count": max(
                int(metrics.get("acquired_count", len(prepared))) - int(metrics.get("persisted_count", persisted_count)),
                0,
            ),
            "observed_count": int(metrics.get("observed_count", 0)),
            "new_count": int(metrics.get("new_count", 0)),
            "changed_count": int(metrics.get("changed_count", 0)),
            "unchanged_count": int(metrics.get("unchanged_count", 0)),
        }
        if email_result:
            result.update(email_result)
        return {"data": result}
    except HTTPException:
        raise
    except Exception as exc:
        repo.fail_run(
            run.id,
            error_summary=f"manual_ingestion_error:{type(exc).__name__}",
            acquired_count=len(prepared),
            persisted_count=persisted_count,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "MANUAL_INGESTION_FAILED",
                "message": "The job could not be processed. The failed acquisition is recorded; retry with a new Idempotency-Key after correcting the input.",
            },
        ) from exc
