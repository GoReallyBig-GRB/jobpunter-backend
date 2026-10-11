"""OpenAI adapter implementing the shared JobPunter AI provider port."""
from __future__ import annotations

import json
import os
import time
from typing import Any

from openai import AsyncOpenAI

from app.application.ai.task_registry import get_ai_task_definition
from app.application.ai.system_policy import SYSTEM_POLICY
from app.ports.ai import (
    AIProviderFailure,
    AIProviderNotConfigured,
    InvalidStructuredOutput,
    ProviderResult,
)


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON constant: {value}")


async def generate_with_openai(
    *,
    task: str,
    prompt: str,
    schema_description: str | None,
    json_mode: bool,
    max_tokens: int,
    model_override: str | None = None,
    task_instruction_override: str | None = None,
    temperature_override: float | None = None,
) -> ProviderResult:
    api_key = os.getenv("JOBPUNTER_OPENAI_API_KEY", "").strip()
    model = (model_override or os.getenv("JOBPUNTER_OPENAI_MODEL", "gpt-4.1-mini")).strip()
    if not api_key or not model:
        raise AIProviderNotConfigured("OpenAI generation is not configured.")

    task_definition = get_ai_task_definition(task)
    structured_mode = json_mode or schema_description is not None
    system_instruction = (
        (("Governed additional instructions, subordinate to system and task policy: "
          + task_instruction_override + "\n\n") if task_instruction_override else "")
        + SYSTEM_POLICY + "\n\nTask policy: " + task_definition.system_instruction
    )
    client = AsyncOpenAI(api_key=api_key, timeout=45.0)
    started = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ],
            temperature=(
                temperature_override if temperature_override is not None
                else task_definition.temperature
            ),
            max_tokens=max_tokens,
            response_format={"type": "json_object"} if structured_mode else {"type": "text"},
        )
    except Exception as exc:
        raise AIProviderFailure("The OpenAI provider request failed.") from exc
    finally:
        await client.close()

    raw_text = response.choices[0].message.content or ""
    if not raw_text.strip():
        raise AIProviderFailure("The OpenAI provider returned an empty response.")
    parsed_data: Any | None = None
    if structured_mode:
        try:
            parsed_data = json.loads(raw_text, parse_constant=_reject_nonstandard_json_constant)
        except (json.JSONDecodeError, ValueError) as exc:
            raise InvalidStructuredOutput("The OpenAI provider returned invalid JSON.") from exc
        if not isinstance(parsed_data, (dict, list)):
            raise InvalidStructuredOutput("The OpenAI provider returned an unsupported JSON root.")
    return ProviderResult(
        raw_text=raw_text,
        parsed_data=parsed_data,
        model_used=model,
        provider_used="openai",
        latency_ms=int((time.monotonic() - started) * 1000),
    )
