import math

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class DomainError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, details: dict | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


async def domain_error_handler(request: Request, exc: DomainError):
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details, "request_id": request_id}},
    )


def _sanitize_non_finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _sanitize_non_finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_non_finite(item) for item in value]
    return value


async def request_validation_error_handler(request: Request, exc: RequestValidationError):
    # Pydantic includes rejected input values in validation errors. Non-finite
    # floats are not valid JSON numbers and otherwise make the 422 response fail.
    # Validation details can include rejected values and arbitrary exception context.
    # Return only stable diagnostic metadata; never echo submitted private content.
    errors = _sanitize_non_finite(exc.errors())
    safe_errors = [
        {key: error[key] for key in ("type", "loc", "msg") if key in error}
        for error in errors
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(safe_errors)},
    )
