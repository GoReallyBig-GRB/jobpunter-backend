from fastapi import FastAPI
from app.api.errors import domain_error_handler, DomainError
from app.api.middleware import request_id_middleware
from app.api.v1.router import router

app = FastAPI(title="JobPunter API", version="0.1.0")
app.middleware("http")(request_id_middleware)
app.add_exception_handler(DomainError, domain_error_handler)
app.include_router(router)
