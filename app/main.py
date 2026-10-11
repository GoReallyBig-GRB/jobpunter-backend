from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.errors import (
    DomainError,
    domain_error_handler,
    request_validation_error_handler,
)
from app.api.middleware import (
    RequestBodyLimitMiddleware,
    SecurityHeadersMiddleware,
    request_id_middleware,
)
from app.api.v1.router import router

app = FastAPI(title="JobPunter API", version="0.1.0")
app.add_middleware(RequestBodyLimitMiddleware, max_bytes=100_000)
# Register last so it wraps earlier ASGI middleware and also covers early 400/413 responses.
app.add_middleware(SecurityHeadersMiddleware)
app.middleware("http")(request_id_middleware)
app.add_exception_handler(DomainError, domain_error_handler)
app.add_exception_handler(RequestValidationError, request_validation_error_handler)
app.include_router(router)
