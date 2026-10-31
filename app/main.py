import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

from app.api.errors import (
    DomainError,
    admin_http_exception_handler,
    admin_unhandled_exception_handler,
    domain_error_handler,
    request_validation_error_handler,
)
from app.api.middleware import (
    RequestBodyLimitMiddleware,
    SecurityHeadersMiddleware,
    parse_cors_origins,
    request_id_middleware,
)
from app.api.v1.router import router

app = FastAPI(title="JobPunter API", version="0.1.0")
cors_origins = parse_cors_origins(os.getenv("JOBPUNTER_CORS_ALLOWED_ORIGINS", ""))
if cors_origins:
    # No wildcard origins or credentialed cross-origin requests are permitted.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
app.add_middleware(RequestBodyLimitMiddleware, max_bytes=100_000)
# Register last so it wraps earlier ASGI middleware and also covers early 400/413 responses.
app.add_middleware(SecurityHeadersMiddleware)
app.middleware("http")(request_id_middleware)
app.add_exception_handler(DomainError, domain_error_handler)
app.add_exception_handler(HTTPException, admin_http_exception_handler)
app.add_exception_handler(Exception, admin_unhandled_exception_handler)
app.add_exception_handler(RequestValidationError, request_validation_error_handler)
app.include_router(router)
