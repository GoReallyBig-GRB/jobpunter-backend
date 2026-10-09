from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_principal
from app.application.jobs.email_parser import parse_job_alert_email
from app.ports.identity import AuthenticatedPrincipal

router = APIRouter(tags=["job email parsing"])


class EmailParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(default="", max_length=500)
    content: str = Field(default="", max_length=80_000)
    sender: str = Field(default="alerts@jobplatform.com", max_length=500)


@router.post("/email/parse-job-alert", response_model=dict)
def parse_job_alert(
    payload: EmailParseRequest,
    _principal: AuthenticatedPrincipal = Depends(get_principal),
) -> dict[str, Any]:
    """Parse submitted email content; this endpoint does not persist or submit applications."""
    return parse_job_alert_email(payload.subject, payload.content, payload.sender)
