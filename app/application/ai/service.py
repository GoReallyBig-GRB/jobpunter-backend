"""Governed AI task execution with version traceability and privacy-minimal telemetry."""
from __future__ import annotations

import os
import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.ai.schemas import GenerateRequest, GenerateResponse
from app.application.ai.system_policy import SYSTEM_POLICY, SYSTEM_POLICY_VERSION
from app.application.ai.task_registry import get_ai_task_definition
from app.application.ai.input_contracts import (
    INPUT_CONTRACT_VERSION, InvalidTaskInput, get_task_input_schema, validate_task_input,
)
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
    if not isinstance(payload, dict):
        raise HTTPException(status_code=503, detail={"code": "AI_CONFIG_INVALID", "message": "AI task configuration is invalid."})
    stored_fingerprint = getattr(row, "payload_fingerprint", None)
    if stored_fingerprint is not None:
        try:
            canonical_payload = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            actual_fingerprint = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            actual_fingerprint = None
        if actual_fingerprint != stored_fingerprint:
            raise HTTPException(status_code=503, detail={"code": "AI_CONFIG_INVALID", "message": "AI task configuration is invalid."})
    allowed = {"system_instruction", "model", "provider", "temperature", "fallback_provider", "fallback_model"}
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
                or (provider == "openai" and model.startswith("gpt-"))
                or (provider is None and model.startswith(("gemini-", "gpt-")))
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
    fallback_provider = payload.get("fallback_provider")
    fallback_model = payload.get("fallback_model")
    if (fallback_provider is None) != (fallback_model is None):
        raise HTTPException(status_code=503, detail={"code": "AI_CONFIG_INVALID", "message": "AI task configuration is invalid."})
    if fallback_provider is not None:
        if not isinstance(fallback_provider, str) or fallback_provider not in {"google", "openai"}:
            raise HTTPException(status_code=503, detail={"code": "AI_CONFIG_INVALID", "message": "AI task configuration is invalid."})
        valid_fallback_model = (
            isinstance(fallback_model, str) and len(fallback_model) <= 110
            and (
                (fallback_provider == "google" and fallback_model.startswith("gemini-"))
                or (fallback_provider == "openai" and fallback_model.startswith("gpt-"))
            )
        )
        if not valid_fallback_model or provider is None or provider == fallback_provider:
            raise HTTPException(status_code=503, detail={"code": "AI_CONFIG_INVALID", "message": "AI task configuration is invalid."})
    return row, payload


async def _generate_with_fallback(
    *,
    provider: str,
    fallback_provider: str | None,
    fallback_model: str | None,
    task: str,
    prompt: str,
    schema_description: str | None,
    max_tokens: int,
    model_override: str | None,
    task_instruction_override: str | None,
    temperature_override: float | None,
    fallback_state: dict[str, bool],
) -> tuple[Any, bool]:
    """Retry once through the explicitly configured alternate provider on provider/JSON failures."""
    try:
        result = await get_ai_provider(provider).generate(
            task=task,
            prompt=prompt,
            schema_description=schema_description,
            json_mode=True,
            max_tokens=max_tokens,
            model_override=model_override,
            task_instruction_override=task_instruction_override,
            temperature_override=temperature_override,
        )
        return result, False
    except (AIProviderNotConfigured, AIProviderFailure, InvalidStructuredOutput):
        if not fallback_provider or not fallback_model:
            raise
    fallback_state["attempted"] = True
    result = await get_ai_provider(fallback_provider).generate(
        task=task,
        prompt=prompt,
        schema_description=schema_description,
        json_mode=True,
        max_tokens=max_tokens,
        model_override=fallback_model,
        task_instruction_override=task_instruction_override,
        temperature_override=temperature_override,
    )
    return result, True


async def execute_ai_task(
    request: GenerateRequest,
    *,
    db: Session,
    user_id: UUID | None,
    request_id: str | None = None,
    commit_before_call: bool = True,
) -> GenerateResponse:
    try:
        task_input = validate_task_input(request.task, request.prompt)
    except InvalidTaskInput as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "AI_INVALID_INPUT", "message": "The task input did not satisfy its server-owned input contract."},
        ) from exc
    prompt = (
        json.dumps(task_input, ensure_ascii=False, separators=(",", ":"))
        if get_task_input_schema(request.task) is not None
        else request.prompt
    )
    input_fingerprint = hashlib.sha256(
        json.dumps(task_input, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        .encode("utf-8")
    ).hexdigest()
    config_row, config = _published_task_config(db, request.task)
    configured_model = config.get("model")
    inferred_provider = "openai" if isinstance(configured_model, str) and configured_model.startswith("gpt-") else None
    provider = str(config.get("provider") or inferred_provider or os.getenv("JOBPUNTER_AI_PROVIDER", "google")).strip().lower()
    default_model_env = "JOBPUNTER_OPENAI_MODEL" if provider == "openai" else "JOBPUNTER_GOOGLE_AI_MODEL"
    default_model = "gpt-4.1-mini" if provider == "openai" else "gemini-2.5-flash"
    model = str(config.get("model") or os.getenv(default_model_env, default_model).strip())
    definition = get_ai_task_definition(request.task)
    effective_config = {
        "task": request.task,
        "task_policy_version": definition.policy_version,
        "system_policy_version": SYSTEM_POLICY_VERSION,
        "system_policy": SYSTEM_POLICY,
        "task_instruction": definition.system_instruction,
        "task_temperature": config.get("temperature", definition.temperature),
        "provider": provider,
        "model": model,
        "fallback_provider": config.get("fallback_provider"),
        "fallback_model": config.get("fallback_model"),
        "system_instruction_override": config.get("system_instruction"),
        "published_config_version_id": str(config_row.id) if config_row else None,
        "input_contract_version": INPUT_CONTRACT_VERSION,
        "input_schema": get_task_input_schema(request.task),
        "output_contract_version": get_task_output_contract_version(request.task),
        "output_schema": definition.output_schema,
        "validation_contract_version": get_task_output_contract_version(request.task),
    }
    config_fingerprint = hashlib.sha256(
        json.dumps(effective_config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    execution = AIExecution(
        user_id=user_id,
        task=request.task,
        task_policy_version=definition.policy_version,
        system_policy_version=SYSTEM_POLICY_VERSION,
        input_contract_version=INPUT_CONTRACT_VERSION,
        input_fingerprint=input_fingerprint,
        output_contract_version=get_task_output_contract_version(request.task),
        config_fingerprint=config_fingerprint,
        contract_snapshot=effective_config,
        config_version_id=config_row.id if config_row else None,
        provider=provider,
        model=model,
        status="RUNNING",
        fallback_attempted=False,
        fallback_used=False,
        request_id=request_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(execution)
    # Standalone executions commit RUNNING before the provider call. Compound
    # workflows may keep execution and domain state in one transaction.
    if commit_before_call:
        db.commit()
        db.refresh(execution)
    else:
        db.flush()

    fallback_used = False
    fallback_state = {"attempted": False}
    try:
        result, fallback_used = await _generate_with_fallback(
            provider=provider,
            fallback_provider=config.get("fallback_provider"),
            fallback_model=config.get("fallback_model"),
            task=request.task,
            prompt=prompt,
            schema_description=request.schema_description,
            max_tokens=request.options.max_tokens,
            model_override=config.get("model"),
            task_instruction_override=config.get("system_instruction"),
            temperature_override=config.get("temperature"),
            fallback_state=fallback_state,
        )
    except AIProviderNotConfigured as exc:
        execution.status = "FAILED"
        execution.error_code = "AI_NOT_CONFIGURED"
        execution.validation_result = {"status": "NOT_RUN", "category": "PROVIDER_NOT_CONFIGURED"}
        execution.fallback_attempted = fallback_state["attempted"]
        execution.finished_at = datetime.now(timezone.utc)
        if commit_before_call:
            db.commit()
        raise HTTPException(status_code=503, detail={"code": "AI_NOT_CONFIGURED", "message": "AI service is temporarily unavailable."}) from exc
    except AIProviderFailure as exc:
        execution.status = "FAILED"
        execution.error_code = "AI_PROVIDER_FAILURE"
        execution.validation_result = {"status": "NOT_RUN", "category": "PROVIDER_FAILURE"}
        execution.fallback_attempted = fallback_state["attempted"]
        execution.finished_at = datetime.now(timezone.utc)
        if commit_before_call:
            db.commit()
        raise HTTPException(status_code=502, detail={"code": "AI_PROVIDER_FAILURE", "message": "AI generation failed. Please try again."}) from exc
    except InvalidStructuredOutput as exc:
        execution.status = "FAILED"
        execution.error_code = "AI_INVALID_JSON"
        execution.validation_result = {"status": "REJECTED", "category": "INVALID_STRUCTURED_OUTPUT"}
        execution.fallback_attempted = fallback_state["attempted"]
        execution.finished_at = datetime.now(timezone.utc)
        if commit_before_call:
            db.commit()
        raise HTTPException(status_code=502, detail={"code": "AI_INVALID_JSON", "message": "The AI response was not valid JSON."}) from exc
    except Exception:
        execution.status = "FAILED"
        execution.error_code = "AI_INTERNAL_FAILURE"
        execution.validation_result = {"status": "NOT_RUN", "category": "INTERNAL_FAILURE"}
        execution.fallback_attempted = fallback_state["attempted"]
        execution.finished_at = datetime.now(timezone.utc)
        if commit_before_call:
            db.commit()
        raise

    # Record the selected route before output validation so rejected fallback
    # output is still accurately attributed in durable telemetry.
    execution.fallback_attempted = fallback_state["attempted"]
    execution.fallback_used = fallback_used
    parsed_data = result.parsed_data
    # Every production AI task uses its registered output contract; free-text mode
    # remains available only at the provider adapter boundary, not through this service.
    should_validate_output = True
    try:
        if should_validate_output:
            if parsed_data is None:
                raise InvalidTaskOutput(request.task)
            parsed_data = validate_task_output(request.task, parsed_data, task_input)
    except InvalidTaskOutput as exc:
        execution.status = "FAILED"
        execution.error_code = "AI_INVALID_OUTPUT"
        execution.validation_result = {
            "status": "REJECTED",
            "category": "OUTPUT_CONTRACT_INVALID",
            "contract_version": get_task_output_contract_version(request.task),
        }
        execution.finished_at = datetime.now(timezone.utc)
        if commit_before_call:
            db.commit()
        raise HTTPException(
            status_code=502,
            detail={"code": "AI_INVALID_OUTPUT", "message": "The AI response did not satisfy the task output contract."},
        ) from exc

    execution.status = "SUCCEEDED"
    execution.validation_result = {
        "status": "PASSED",
        "contract_version": get_task_output_contract_version(request.task),
    }
    execution.fallback_used = fallback_used
    execution.provider = result.provider_used
    execution.model = result.model_used
    execution.latency_ms = result.latency_ms
    execution.finished_at = datetime.now(timezone.utc)
    if commit_before_call:
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
        isFallback=fallback_used,
    )
