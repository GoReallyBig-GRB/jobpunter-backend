from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_principal
from app.api.v1.admin import _require_admin
from app.application.support.service import (
    SupportConflict, add_message, change_ticket_status, create_ticket,
    mark_ticket_read, ticket_dict, ticket_messages,
)
from app.infrastructure.db.models import SupportTicket, User
from app.infrastructure.db.session import get_db
from app.ports.identity import AuthenticatedPrincipal

candidate_router = APIRouter(prefix="/candidate/support/tickets", tags=["candidate support"])
admin_router = APIRouter(prefix="/admin/support/tickets", tags=["admin support"])


class TicketCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=8, max_length=200)
    title: str = Field(min_length=3, max_length=200)
    issue_details: str = Field(min_length=3, max_length=10000)
    follow_up_to_ticket_id: UUID | None = None

    @field_validator("title", "issue_details")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Text must not be blank.")
        return value.strip()


class MessageCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=8, max_length=200)
    body: str = Field(min_length=1, max_length=10000)

    @field_validator("body")
    @classmethod
    def nonblank_body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must not be blank.")
        return value.strip()


class TicketStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(pattern="^(OPEN|RESOLVED|CLOSED)$")


def _ticket_or_404(db: Session, ticket_id: UUID) -> SupportTicket:
    ticket = db.get(SupportTicket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Support ticket not found")
    return ticket


def _candidate_ticket(db: Session, ticket_id: UUID, user_id: UUID) -> SupportTicket:
    ticket = db.scalar(select(SupportTicket).where(
        SupportTicket.id == ticket_id,
        SupportTicket.candidate_user_id == user_id,
    ))
    if ticket is None:
        raise HTTPException(status_code=404, detail="Support ticket not found")
    return ticket


def _candidate_payload(ticket: SupportTicket, db: Session) -> dict:
    mark_ticket_read(db, ticket, reader_role="CANDIDATE")
    return {"data": ticket_dict(ticket, ticket_messages(db, ticket.id))}


def _admin_payload(ticket: SupportTicket, db: Session) -> dict:
    mark_ticket_read(db, ticket, reader_role="ADMIN")
    candidate = db.get(User, ticket.candidate_user_id)
    payload = ticket_dict(ticket, ticket_messages(db, ticket.id))
    payload["candidate"] = {
        "id": str(candidate.id),
        "display_name": candidate.display_name,
        "email": candidate.email,
    } if candidate else None
    return {"data": payload}


@candidate_router.post("", status_code=201)
def candidate_create_ticket(
    payload: TicketCreateRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    try:
        ticket, created = create_ticket(
            db, principal.user_id,
            idempotency_key=payload.idempotency_key,
            title=payload.title,
            issue_details=payload.issue_details,
            follow_up_to_ticket_id=payload.follow_up_to_ticket_id,
        )
    except SupportConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"data": ticket_dict(ticket, ticket_messages(db, ticket.id)), "created": created}


@candidate_router.get("")
def candidate_list_tickets(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status", pattern="^(OPEN|RESOLVED|CLOSED)$"),
    unread_only: bool = Query(default=False),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    filters = [SupportTicket.candidate_user_id == principal.user_id]
    if status_filter:
        filters.append(SupportTicket.status == status_filter)
    if unread_only:
        filters.append(SupportTicket.candidate_unread.is_(True))
    total = int(db.scalar(select(func.count()).select_from(SupportTicket).where(*filters)) or 0)
    rows = db.scalars(select(SupportTicket).where(*filters).order_by(
        SupportTicket.updated_at.desc(), SupportTicket.id.desc()
    ).offset(offset).limit(limit)).all()
    return {"data": [ticket_dict(row) for row in rows],
            "pagination": {"limit": limit, "offset": offset, "total": total}}


@candidate_router.get("/{ticket_id}")
def candidate_get_ticket(
    ticket_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    ticket = _candidate_ticket(db, ticket_id, principal.user_id)
    return _candidate_payload(ticket, db)


@candidate_router.post("/{ticket_id}/messages", status_code=201)
def candidate_add_message(
    ticket_id: UUID,
    payload: MessageCreateRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    ticket = _candidate_ticket(db, ticket_id, principal.user_id)
    try:
        message, created = add_message(
            db, ticket, sender_id=principal.user_id, sender_role="CANDIDATE",
            idempotency_key=payload.idempotency_key, body=payload.body,
        )
    except SupportConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"data": {"id": str(message.id), "body": message.body,
                     "delivery_status": message.delivery_status,
                     "created_at": message.created_at.isoformat()}, "created": created}


@candidate_router.patch("/{ticket_id}/status")
def candidate_change_status(
    ticket_id: UUID,
    payload: TicketStatusRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    if payload.status not in {"RESOLVED", "CLOSED"}:
        raise HTTPException(status_code=422, detail="Candidates may only resolve or close a ticket.")
    ticket = _candidate_ticket(db, ticket_id, principal.user_id)
    try:
        changed = change_ticket_status(
            db, ticket, actor_id=principal.user_id, actor_role="CANDIDATE",
            target_status=payload.status,
        )
    except SupportConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"data": ticket_dict(ticket, ticket_messages(db, ticket.id)), "changed": changed}


@admin_router.get("")
def admin_list_tickets(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status", pattern="^(OPEN|RESOLVED|CLOSED)$"),
    unread_only: bool = Query(default=False),
    search: str | None = Query(default=None, max_length=200),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    _require_admin(db, principal)
    filters = []
    if status_filter:
        filters.append(SupportTicket.status == status_filter)
    if unread_only:
        filters.append(SupportTicket.admin_unread.is_(True))
    if search and search.strip():
        term = f"%{search.strip()}%"
        matching_candidates = select(User.id).where(
            or_(User.display_name.ilike(term), User.email.ilike(term))
        )
        filters.append(or_(
            SupportTicket.title.ilike(term),
            SupportTicket.issue_details.ilike(term),
            SupportTicket.candidate_user_id.in_(matching_candidates),
        ))
    total = len(db.scalars(select(SupportTicket.id).where(*filters)).all())
    rows = db.scalars(select(SupportTicket).where(*filters).order_by(
        SupportTicket.updated_at.desc(), SupportTicket.id.desc()
    ).offset(offset).limit(limit)).all()
    return {"data": [ticket_dict(row) for row in rows],
            "pagination": {"limit": limit, "offset": offset, "total": total}}


@admin_router.get("/{ticket_id}")
def admin_get_ticket(
    ticket_id: UUID,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    _require_admin(db, principal)
    return _admin_payload(_ticket_or_404(db, ticket_id), db)


@admin_router.post("/{ticket_id}/messages", status_code=201)
def admin_add_message(
    ticket_id: UUID,
    payload: MessageCreateRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    _require_admin(db, principal)
    ticket = _ticket_or_404(db, ticket_id)
    try:
        message, created = add_message(
            db, ticket, sender_id=principal.user_id, sender_role="ADMIN",
            idempotency_key=payload.idempotency_key, body=payload.body,
        )
    except SupportConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"data": {"id": str(message.id), "body": message.body,
                     "delivery_status": message.delivery_status,
                     "created_at": message.created_at.isoformat()}, "created": created}


@admin_router.patch("/{ticket_id}/status")
def admin_change_status(
    ticket_id: UUID,
    payload: TicketStatusRequest,
    principal: AuthenticatedPrincipal = Depends(get_principal),
    db: Session = Depends(get_db),
) -> dict:
    _require_admin(db, principal)
    ticket = _ticket_or_404(db, ticket_id)
    try:
        changed = change_ticket_status(
            db, ticket, actor_id=principal.user_id, actor_role="ADMIN",
            target_status=payload.status,
        )
    except SupportConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"data": ticket_dict(ticket, ticket_messages(db, ticket.id)), "changed": changed}
