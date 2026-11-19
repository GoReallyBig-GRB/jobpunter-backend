"""Google AI provider adapter for the system-managed AI task service.

Provider credentials and model routing are read only from the backend environment.
Candidate-facing requests cannot select a provider or model.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

from google import genai
from google.genai import types


from app.application.ai.task_registry import get_ai_task_definition
from app.ports.ai import AIProviderFailure, AIProviderNotConfigured, InvalidStructuredOutput, ProviderResult
from app.application.ai.system_policy import SYSTEM_POLICY


logger = logging.getLogger(__name__)

# Tasks whose provider input is public job-posting text only (no candidate data).
_PUBLIC_INPUT_TASKS = frozenset({"job_requirement_extraction"})


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"Non-standard JSON constant: {value}")


async def generate_with_google(
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
    api_key = os.getenv("JOBPUNTER_GOOGLE_AI_API_KEY", "").strip()
    model = (model_override or os.getenv("JOBPUNTER_GOOGLE_AI_MODEL", "gemini-3.5-flash-lite")).strip()
    if not api_key or not model:
        raise AIProviderNotConfigured("AI generation is not configured.")

    client = genai.Client(api_key=api_key, http_options={"timeout": 45_000})
    # Caller-provided descriptions may request structured mode for compatibility, but cannot
    # replace the server-owned schema registered for this task.
    structured_mode = json_mode or schema_description is not None
    contents = prompt

    started = time.monotonic()
    try:
        response = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=(temperature_override if temperature_override is not None else get_ai_task_definition(task).temperature),
                max_output_tokens=max_tokens,
                response_mime_type="application/json" if structured_mode else None,
                response_json_schema=get_ai_task_definition(task).output_schema if structured_mode else None,
                system_instruction=((("Governed additional instructions, subordinate to the safety and task policies below: " + task_instruction_override + "\n\n") if task_instruction_override else "") + SYSTEM_POLICY + "\n\nTask policy: " + get_ai_task_definition(task).system_instruction),
            ),
        )
    except Exception as exc:
        # Operational diagnosis only: exception class, numeric HTTP status and the provider's
        # upper-case status word. Never the message (it can echo request content) or the key.
        code = getattr(exc, "code", None)
        status = getattr(exc, "status", None)
        http_status = code if isinstance(code, int) and not isinstance(code, bool) else None
        logger.warning(
            "google_generation_failed error_class=%s http_status=%s api_status=%s",
            type(exc).__name__,
            http_status,
            status if isinstance(status, str) and re.fullmatch(r"[A-Z_]{3,40}", status) else None,
        )
        # Request-rejection details (for example an unusable schema) are needed to fix a
        # configuration fault. They are logged only for tasks whose input is public job text,
        # only for 4xx responses, truncated, and with the API key removed. Tasks that carry
        # candidate data never log the provider message.
        if task in _PUBLIC_INPUT_TASKS and http_status is not None and 400 <= http_status < 500:
            detail = str(getattr(exc, "message", "") or "").replace(api_key, "[redacted]")
            logger.warning("google_generation_rejected task=%s detail=%s", task, detail[:300])
        raise AIProviderFailure("The AI provider request failed.") from exc

    raw_text = response.text or ""
    if not raw_text.strip():
        raise AIProviderFailure("The AI provider returned an empty response.")

    parsed_data = None
    # JSON mode is a structured-output contract even when the caller does not
    # provide an additional schema description. Never return malformed JSON as
    # a successful free-text response when JSON mode was explicitly requested.
    if structured_mode:
        cleaned = raw_text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned.removeprefix("```json").removesuffix("```").strip()
        elif cleaned.startswith("```"):
            cleaned = cleaned.removeprefix("```").removesuffix("```").strip()
        try:
            parsed_data = json.loads(
                cleaned,
                parse_constant=_reject_nonstandard_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise InvalidStructuredOutput("The AI provider returned invalid JSON.") from exc
        if not isinstance(parsed_data, (dict, list)):
            raise InvalidStructuredOutput(
                "The AI provider returned JSON with an unsupported root type."
            )

    return ProviderResult(
        raw_text=raw_text,
        parsed_data=parsed_data,
        model_used=model,
        provider_used="google",
        latency_ms=int((time.monotonic() - started) * 1000),
    )
