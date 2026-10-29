import math

from fastapi import HTTPException, Request
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


async def admin_http_exception_handler(request: Request, exc: HTTPException):
    context = getattr(request.state, "admin_audit_context", None)
    if context and not getattr(request.state, "admin_denial_audited", False):
        db = context["db"]
        principal = context["principal"]
        try:
            db.rollback()
            from app.infrastructure.db.models.governance import AdminAuditEvent
            from app.infrastructure.db.models.identity import User

            if db.get(User, principal.user_id) is not None:
                detail = exc.detail
                code = detail.get("code") if isinstance(detail, dict) else None
                if not isinstance(code, str) or not code:
                    code = f"HTTP_{exc.status_code}"
                outcome = (
                    "DENIED" if exc.status_code in (401, 403)
                    else "VALIDATION_FAILED" if exc.status_code == 422
                    else "REJECTED"
                )
                db.add(AdminAuditEvent(
                    actor_user_id=principal.user_id,
                    action="ADMIN_REQUEST_REJECTED",
                    resource_type="admin_route",
                    resource_id=None,
                    outcome=outcome,
                    details={
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": exc.status_code,
                        "error_code": code[:100],
                    },
                    request_id=request.headers.get("x-request-id"),
                ))
                db.commit()
        except Exception:
            db.rollback()
            return JSONResponse(
                status_code=500,
                content={"detail": "Administrative request failure could not be audited."},
            )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": jsonable_encoder(exc.detail)},
        headers=exc.headers,
    )


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

    # Admin route-level dependencies capture an authenticated principal and the
    # request-scoped DB session before validation is reported. Persist only field
    # paths and validator error types; never store submitted values or error text.
    context = getattr(request.state, "admin_audit_context", None)
    if context:
        db = context["db"]
        principal = context["principal"]
        try:
            db.rollback()
            from app.infrastructure.db.models.governance import AdminAuditEvent
            from app.infrastructure.db.models.identity import User

            if db.get(User, principal.user_id) is not None:
                db.add(AdminAuditEvent(
                    actor_user_id=principal.user_id,
                    action="ADMIN_REQUEST_VALIDATION_FAILED",
                    resource_type="admin_route",
                    resource_id=None,
                    outcome="VALIDATION_FAILED",
                    details={
                        "method": request.method,
                        "path": request.url.path,
                        "errors": [
                            {
                                "type": str(error.get("type", "validation_error"))[:100],
                                "loc": [str(part)[:100] for part in error.get("loc", ())],
                            }
                            for error in errors
                        ],
                    },
                    request_id=request.headers.get("x-request-id"),
                ))
                db.commit()
        except Exception:
            db.rollback()
            return JSONResponse(
                status_code=500,
                content={"detail": "Administrative validation failure could not be audited."},
            )

    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(safe_errors)},
    )
