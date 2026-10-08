from fastapi import FastAPI

from app.api.errors import DomainError, domain_error_handler
from app.api.middleware import request_id_middleware
from app.config.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="0.2.0")
    app.middleware("http")(request_id_middleware)
    app.add_exception_handler(DomainError, domain_error_handler)

    @app.get("/health")
    def health():
        return {"data": {"status": "ok", "environment": settings.environment}}

    return app


app = create_app()
