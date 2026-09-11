"""Operational and observability API endpoints for Day 26."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.guardrails import default_guardrail_registry
from app.models import AgentTask, AgentToolCall, Event, GuardrailViolation, Item, Source

router = APIRouter(prefix="/ops", tags=["ops"])
DbSession = Annotated[Session, Depends(get_db)]


class ListResponse(BaseModel):
    status: str = "ok"
    count: int
    items: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


@router.get("/guardrails/rules", response_model=ListResponse)
def list_guardrail_rules() -> ListResponse:
    items = [rule.model_dump(mode="json") for rule in default_guardrail_registry.list_rules()]
    return ListResponse(count=len(items), items=items)


@router.get("/guardrails/violations", response_model=ListResponse)
def list_guardrail_violations(
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> ListResponse:
    try:
        rows = db.scalars(
            select(GuardrailViolation)
            .order_by(desc(GuardrailViolation.created_at))
            .limit(limit)
        ).all()
    except SQLAlchemyError as exc:
        return _db_unavailable(exc)
    items = [
        {
            "id": row.id,
            "rule_name": row.rule_name,
            "rule_type": row.rule_type,
            "severity": row.severity,
            "message": row.message,
            "subject_type": row.subject_type,
            "subject_id": row.subject_id,
            "resolved": row.resolved,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return ListResponse(count=len(items), items=items)


@router.get("/sources", response_model=ListResponse)
def list_sources(
    db: DbSession,
    limit: int = Query(default=100, ge=1, le=500),
) -> ListResponse:
    try:
        rows = db.scalars(select(Source).order_by(Source.name).limit(limit)).all()
    except SQLAlchemyError as exc:
        return _db_unavailable(exc)
    items = [
        {
            "id": row.id,
            "name": row.name,
            "source_type": row.source_type,
            "source_status": row.source_status,
            "source_origin": row.source_origin,
            "platform": row.platform,
            "is_active": row.is_active,
        }
        for row in rows
    ]
    return ListResponse(count=len(items), items=items)


@router.get("/items", response_model=ListResponse)
def list_items(
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> ListResponse:
    try:
        rows = db.scalars(select(Item).order_by(desc(Item.created_at)).limit(limit)).all()
    except SQLAlchemyError as exc:
        return _db_unavailable(exc)
    items = [
        {
            "id": row.id,
            "source_id": row.source_id,
            "title": row.title,
            "url": row.url,
            "source_status": row.source_status,
            "source_origin": row.source_origin,
            "signal_role": row.signal_role,
            "quality_flags": row.quality_flags_json or [],
            "published_at": row.published_at.isoformat() if row.published_at else None,
            "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        }
        for row in rows
    ]
    return ListResponse(count=len(items), items=items)


@router.get("/events", response_model=ListResponse)
def list_events(
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> ListResponse:
    try:
        rows = db.scalars(select(Event).order_by(desc(Event.updated_at)).limit(limit)).all()
    except SQLAlchemyError as exc:
        return _db_unavailable(exc)
    items = [
        {
            "id": row.id,
            "title": row.title,
            "lifecycle_status": row.lifecycle_status,
            "confidence_score": row.confidence_score,
            "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
            "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
            "event_detail": row.event_detail_json or {},
        }
        for row in rows
    ]
    return ListResponse(count=len(items), items=items)


@router.get("/agent-tasks", response_model=ListResponse)
def list_agent_tasks(
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> ListResponse:
    try:
        rows = db.scalars(select(AgentTask).order_by(desc(AgentTask.created_at)).limit(limit)).all()
    except SQLAlchemyError as exc:
        return _db_unavailable(exc)
    items = [
        {
            "id": row.id,
            "task_type": row.task_type,
            "status": row.status,
            "title": row.title,
            "plan_id": row.plan_id,
            "retry_count": row.retry_count,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return ListResponse(count=len(items), items=items)


@router.get("/tool-calls", response_model=ListResponse)
def list_tool_calls(
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> ListResponse:
    try:
        rows = db.scalars(
            select(AgentToolCall).order_by(desc(AgentToolCall.created_at)).limit(limit)
        ).all()
    except SQLAlchemyError as exc:
        return _db_unavailable(exc)
    items = [
        {
            "id": row.id,
            "task_id": row.task_id,
            "tool_name": row.tool_name,
            "status": row.status,
            "attempt": row.attempt,
            "duration_ms": row.duration_ms,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return ListResponse(count=len(items), items=items)


def _db_unavailable(exc: SQLAlchemyError) -> ListResponse:
    return ListResponse(status="unavailable", count=0, items=[], error=str(exc))
