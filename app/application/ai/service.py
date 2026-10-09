from app.application.ai.schemas import GenerateRequest, GenerateResponse
from app.infrastructure.ai.google_generation import (
    AIProviderFailure,
    AIProviderNotConfigured,
    InvalidStructuredOutput,
    generate_with_google,
)


async def execute_ai_task(request: GenerateRequest) -> GenerateResponse:
    try:
        result = await generate_with_google(
            task=request.task,
            prompt=request.prompt,
            schema_description=request.schema_description,
            json_mode=request.options.json_mode,
            max_tokens=request.options.max_tokens,
        )
    except AIProviderNotConfigured as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail={"code": "AI_NOT_CONFIGURED", "message": "AI service is temporarily unavailable."},
        ) from exc
    except AIProviderFailure as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=502,
            detail={"code": "AI_PROVIDER_FAILURE", "message": "AI generation failed. Please try again."},
        ) from exc
    except InvalidStructuredOutput as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=502,
            detail={"code": "AI_INVALID_JSON", "message": "The AI response was not valid JSON."},
        ) from exc

    return GenerateResponse(
        rawText=result.raw_text,
        parsedData=result.parsed_data,
        modelUsed=result.model_used,
        providerUsed=result.provider_used,
        latencyMs=result.latency_ms,
    )
