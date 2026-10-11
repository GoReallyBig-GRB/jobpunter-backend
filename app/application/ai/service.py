"""Governed AI task execution with version traceability and privacy-minimal telemetry."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.ai.schemas import GenerateRequest, GenerateResponse
from app.application.ai.output_contracts import (
    InvalidTaskOutput,
    get_task_output_contract_version,
    validate_task_output,
)
from app.infrastructure.ai.provider_registry import get_ai_provider
from app.ports.ai import (
    AIProviderFailure,
    AIProviderNotConfigured,
    InvalidStructuredOutput,
)
from app.infrastructure.db.models.governance import AIExecution, GovernanceConfigVersion


def _published_task_config(db: Session, task: str) -> tuple[GovernanceConfigVersion | None, dict[str, Any]]:
    row = db.scalar(select(GovernanceConfigVersion).where(
        GovernanceConfigVersion.config_key == f"ai.task.{task}",
        GovernanceConfigVersion.status == "PUBLISHED",
    ))
    if row is None:
        return None, {}
    payload = row.payload
    allowed = {"system_instruction", "model", "provider", "temperature"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise HTTPException(status_code=503, detail={"code": "AI_CONFIG_INVALID", "message": "AI task configuration is invalid."})
    provider = payload.get("provider")
    if provider is not None and provider not in {"google", "openai"}:
        raise HTTPException(status_code=503, detail={"code": "AI_CONFIG_INVALID", "message": "AI task configuration is invalid."})
    model = payload.get("model")
    if model is not None:
        valid_model = (
            isinstance(model, str) and len(model) <= 110
            and (
                (provider == "google" and model.startswith("gemini-"))
                or (provider == "openai" and model.startswith(("gpt-", "o1", "o3", "o4")))
                or (provider is None and model.startswith(("gemini-", "gpt-", "o1", "o3", "o4")))
            )
        )
        if not valid_model:
            raise HTTPException(status_code=503, detail={"code": "AI_CONFIG_INVALID", "message": "AI task configuration is invalid."})
    instruction = payload.get("system_instruction")
    if instruction is not None and (not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 12000):
        raise HTTPException(status_code=503, detail={"code": "AI_CONFIG_INVALID", "message": "AI task configuration is invalid."})
    temperature = payload.get("temperature")
    if temperature is not None and (isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 1):
        raise HTTPException(status_code=503, detail={"code": "AI_CONFIG_INVALID", "message": "AI task configuration is invalid."})
    return row, payload


async def execute_ai_task(
    request: GenerateRequest,
    *,
    db: Session,
    user_id: UUID,
    request_id: str | None = None,
) -> GenerateResponse:
    config_row, config = _published_task_config(db, request.task)
    provider = str(config.get("provider") or os.getenv("JOBPUNTER_AI_PROVIDER", "google")).strip().lower()
    default_model_env = "JOBPUNTER_OPENAI_MODEL" if provider == "openai" else "JOBPUNTER_GOOGLE_AI_MODEL"
    default_model = "gpt-4.1-mini" if provider == "openai" else "gemini-2.5-flash"
    model = str(config.get("model") or os.getenv(default_model_env, default_model).strip())
    execution = AIExecution(
        user_id=user_id,
        task=request.task,
        output_contract_version=get_task_output_contract_version(request.task),
        config_version_id=config_row.id if config_row else None,
        provider=provider,
        model=model,
        status="RUNNING",
        request_id=request_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(execution)
    # Make the RUNNING record durable before spending provider resources.
    db.commit()
    db.refresh(execution)

    try:
        result = await get_ai_provider(provider).generate(
            task=request.task,
            prompt=request.prompt,
            schema_description=request.schema_description,
            # Evidence matching affects eligibility and application decisions; its
            # server-owned contract requires structured output regardless of the
            # caller's requested presentation mode.
            json_mode=True,
            max_tokens=request.options.max_tokens,
            model_override=config.get("model"),
            task_instruction_override=config.get("system_instruction"),
            temperature_override=config.get("temperature"),
        )
    except AIProviderNotConfigured as exc:
        execution.status = "FAILED"
        execution.error_code = "AI_NOT_CONFIGURED"
        execution.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=503, detail={"code": "AI_NOT_CONFIGURED", "message": "AI service is temporarily unavailable."}) from exc
    except AIProviderFailure as exc:
        execution.status = "FAILED"
        execution.error_code = "AI_PROVIDER_FAILURE"
        execution.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=502, detail={"code": "AI_PROVIDER_FAILURE", "message": "AI generation failed. Please try again."}) from exc
    except InvalidStructuredOutput as exc:
        execution.status = "FAILED"
        execution.error_code = "AI_INVALID_JSON"
        execution.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=502, detail={"code": "AI_INVALID_JSON", "message": "The AI response was not valid JSON."}) from exc
    except Exception:
        execution.status = "FAILED"
        execution.error_code = "AI_INTERNAL_FAILURE"
        execution.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise

    parsed_data = result.parsed_data
    # Every production AI task uses its registered output contract; free-text mode
    # remains available only at the provider adapter boundary, not through this service.
    should_validate_output = True
    try:
        if should_validate_output:
            if parsed_data is None:
                raise InvalidTaskOutput(request.task)
            parsed_data = validate_task_output(request.task, parsed_data)
    except InvalidTaskOutput as exc:
        execution.status = "FAILED"
        execution.error_code = "AI_INVALID_OUTPUT"
        execution.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(
            status_code=502,
            detail={"code": "AI_INVALID_OUTPUT", "message": "The AI response did not satisfy the task output contract."},
        ) from exc

    execution.status = "SUCCEEDED"
    execution.provider = result.provider_used
    execution.model = result.model_used
    execution.latency_ms = result.latency_ms
    execution.finished_at = datetime.now(timezone.utc)
    db.commit()
    response_raw_text = result.raw_text
    if request.task == "cover_letter" and isinstance(parsed_data, dict):
        response_raw_text = str(parsed_data.get("content", result.raw_text))
    elif request.task == "cv_generation" and isinstance(parsed_data, dict):
        response_raw_text = str(
            parsed_data.get("content") or parsed_data.get("summary") or result.raw_text
        )

    return GenerateResponse(
        rawText=response_raw_text,
        parsedData=parsed_data,
        modelUsed=result.model_used,
        providerUsed=result.provider_used,
        latencyMs=result.latency_ms,
        executionId=execution.id,
    )
