"""Briefing, health, job control and email APIs for the personal MVP."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.auth import require_admin
from app.core.config import settings
from app.db.session import get_db
from app.models import DailyReport, Event, HumanReviewTask
from app.models.briefing import AnalysisRun, EmailDelivery, ReportRevision
from app.schemas.display import DailyBriefing
from app.services.briefing import approve_report, generate_briefing, save_briefing, source_health
from app.services.briefing_email import email_ready, recover_interrupted_deliveries, send_report
from app.services.briefing_jobs import default_analysis_input, queue_analysis
from app.services.briefing_render import render_report, render_text
from app.services.human_review import task_record
from app.services.request_usage import usage_summary

router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)], tags=["briefing"])
Db = Annotated[Session, Depends(get_db)]


def get_report(db, report_id):
    row = db.get(DailyReport, report_id)
    if row is None:
        raise HTTPException(404, "日报不存在")
    return row


def report_info(db, row, *, full=False):
    revision = db.get(ReportRevision, row.id)
    result = {
        "id": row.id,
        "title": row.title,
        "status": row.status,
        "report_date": row.report_date,
        "created_at": row.created_at,
        "window_end": row.briefing_json.get("window_end"),
        "event_count": row.briefing_json.get("event_count", 0),
        "supersedes_id": revision.supersedes_id if revision else None,
        "quality": row.quality_review_json,
    }
    if full:
        result["briefing"] = row.briefing_json
    return result


@router.get("/dashboard")
def dashboard(db: Db, request: Request):
    latest = db.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.window_end.is_not(None))
        .order_by(AnalysisRun.window_end.desc())
    )
    coordinator = getattr(request.app.state, "coordinator", None)
    return {
        "latest_run": latest.run_id if latest else None,
        "sources": source_health(latest.collection_json or {}, datetime.now(UTC)) if latest else [],
        "pending_reviews": db.scalar(
            select(func.count(HumanReviewTask.id)).where(HumanReviewTask.status == "pending")
        ),
        "scheduler": {
            "enabled": settings.scheduler_enabled,
            "interval_minutes": settings.sampling_interval_minutes,
            "error": coordinator.last_error if coordinator else None,
        },
        "email": {
            "configured": email_ready(),
            "scheduled": settings.email_schedule_enabled,
            "time": settings.email_send_time,
            "timezone": settings.briefing_timezone,
            "recipient_count": len(settings.email_recipients),
        },
        "request_limits": settings.request_limits,
    }


@router.get("/usage")
def usage(db: Db):
    return usage_summary(db)


@router.get("/jobs")
def jobs(db: Db, limit: int = Query(30, ge=1, le=200)):
    return [
        {
            "run_id": row.run_id,
            "status": row.status,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "window_end": row.window_end,
            "report_id": row.report_id,
            "error": row.error,
        }
        for row in db.scalars(
            select(AnalysisRun).order_by(AnalysisRun.started_at.desc()).limit(limit)
        )
    ]


class JobInput(BaseModel):
    run_id: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$"
    )


@router.post("/jobs", status_code=202)
def create_job(db: Db, data: JobInput):
    try:
        row = queue_analysis(db, default_analysis_input(data.run_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run_id": row.run_id, "status": row.status}


@router.get("/reports")
def reports(db: Db, limit: int = Query(30, ge=1, le=200)):
    return [
        report_info(db, row)
        for row in db.scalars(select(DailyReport).order_by(DailyReport.id.desc()).limit(limit))
    ]


@router.get("/reports/latest")
def latest_report(db: Db):
    row = db.scalar(select(DailyReport).order_by(DailyReport.id.desc()))
    if not row:
        raise HTTPException(404, "尚无日报，请先运行一轮采集")
    return report_info(db, row, full=True)


@router.get("/reports/{report_id}")
def report_detail(report_id: int, db: Db):
    return report_info(db, get_report(db, report_id), full=True)


@router.get("/reports/{report_id}/html", response_class=HTMLResponse)
def report_html(report_id: int, db: Db, email: bool = False):
    row = get_report(db, report_id)
    return render_report(
        DailyBriefing.model_validate(row.briefing_json), email=email, status=row.status
    )


@router.get("/reports/{report_id}/text", response_class=PlainTextResponse)
def report_text(report_id: int, db: Db):
    return render_text(DailyBriefing.model_validate(get_report(db, report_id).briefing_json))


@router.post("/reports/{report_id}/approve")
def approve(report_id: int, db: Db):
    try:
        row = approve_report(db, get_report(db, report_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return report_info(db, row)


@router.post("/reports/{report_id}/send")
def send(report_id: int, db: Db):
    try:
        rows = send_report(db, get_report(db, report_id))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return [
        {"id": row.id, "recipient": row.recipient, "status": row.status, "error": row.error}
        for row in rows
    ]


@router.post("/reports/{report_id}/refresh")
def refresh(report_id: int, db: Db):
    from uuid import uuid4

    from app.services.briefing_jobs import acquire_lease, release_lease

    revision = db.get(ReportRevision, report_id)
    if revision is None:
        raise HTTPException(409, "旧日报无法重建，请重新采集")
    # Rebuild only the latest analysis; earlier published versions stay immutable.
    latest = db.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.output_json.is_not(None))
        .order_by(AnalysisRun.window_end.desc())
    )
    if latest is None or latest.run_id != revision.run_id:
        raise HTTPException(409, "请从最新分析轮次生成更正版，历史版本保持原样")
    owner = str(uuid4())
    if not acquire_lease(db, "analysis", owner, seconds=900):
        raise HTTPException(409, "采集分析正在运行，请稍后刷新草稿")
    try:
        row = save_briefing(
            db, generate_briefing(db, latest.run_id, refresh=True), supersedes_id=report_id,
        )
        latest.report_id = row.id
        db.commit()
        return report_info(db, row)
    finally:
        release_lease(db, "analysis", owner)


@router.get("/deliveries")
def deliveries(db: Db, limit: int = Query(50, ge=1, le=200)):
    recover_interrupted_deliveries(db)
    return [
        {
            "id": row.id,
            "report_id": row.report_id,
            "recipient": row.recipient,
            "status": row.status,
            "attempts": row.attempts,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "error": row.error,
            "message_id": row.message_id,
        }
        for row in db.scalars(select(EmailDelivery).order_by(EmailDelivery.id.desc()).limit(limit))
    ]


class DeliveryDecision(BaseModel):
    received: bool


@router.post("/deliveries/{delivery_id}/resolve")
def resolve_delivery(delivery_id: int, data: DeliveryDecision, db: Db):
    row = db.get(EmailDelivery, delivery_id)
    if row is None or row.status != "unknown":
        raise HTTPException(409, "只能核对结果未知的投递")
    row.status = "sent" if data.received else "failed"
    row.error = "管理员已核对收件箱：" + ("已收到" if data.received else "未收到，可手动重试")
    db.commit()
    return {"status": row.status}


@router.get("/review-queue")
def review_queue(db: Db, limit: int = Query(50, ge=1, le=200)):
    active = or_(
        HumanReviewTask.status == "pending",
        HumanReviewTask.payload_json["refresh"]["status"].as_string() == "failed",
    )
    rows = db.scalars(select(HumanReviewTask).where(active)
                      .order_by(HumanReviewTask.id).limit(limit))
    return {
        "total": db.scalar(select(func.count(HumanReviewTask.id)).where(active)),
        "items": [task_record(task) for task in rows],
    }


@router.get("/reviews/{task_id}/context")
def review_context(task_id: int, db: Db):
    task = db.get(HumanReviewTask, task_id)
    if task is None:
        raise HTTPException(404, "审核任务不存在")
    candidate = (task.payload_json or {}).get("candidate_event_id")
    event = db.scalar(select(Event).where(Event.event_id == candidate)) if candidate else None
    return {
        "incoming": task.payload_json,
        "existing": {
            "event_id": event.event_id,
            "title": event.title,
            "citations": event.source_citations_json,
            "detail": event.event_detail_json,
        }
        if event
        else None,
    }


@router.post("/reviews/{task_id}/refresh")
def retry_review_refresh(task_id: int, db: Db):
    from uuid import uuid4

    from app.services.briefing_jobs import acquire_lease, release_lease
    from app.services.review_refresh import refresh_after_review

    task = db.get(HumanReviewTask, task_id)
    if task is None or task.status == "pending":
        raise HTTPException(409, "请先完成候选审核")
    owner = str(uuid4())
    if not acquire_lease(db, "analysis", owner, seconds=900):
        raise HTTPException(409, "采集分析正在运行，请稍后重试")
    try:
        payload = dict(task.payload_json or {})
        report_id = refresh_after_review(
            db, task, payload.get("decision_audit", {}).get("event_id")
        )
        payload["refresh"] = {"status": "refreshed", "report_id": report_id}
        task.payload_json = payload
        db.commit()
        return {"status": "refreshed", "report_id": report_id}
    except Exception as exc:
        db.rollback()
        payload = dict(task.payload_json or {})
        payload["refresh"] = {"status": "failed", "error": type(exc).__name__}
        task.payload_json = payload
        db.commit()
        raise HTTPException(409, "审核决定已保存，重算仍未完成，请稍后重试") from exc
    finally:
        release_lease(db, "analysis", owner)
