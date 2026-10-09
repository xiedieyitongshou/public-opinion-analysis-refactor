"""Opt-in automatic daily snapshot, quality gate, archive and idempotent delivery."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models import DailyReport
from app.models.briefing import AnalysisRun, DailyBriefingJob, DailyDispatch
from app.schemas.display import DailyBriefing
from app.services.briefing import approve_report, as_utc, review_briefing
from app.services.briefing_email import email_ready, send_report, smtp_send
from app.services.briefing_render import render_report, render_text

logger = logging.getLogger(__name__)


def archive_report(report):
    briefing = DailyBriefing.model_validate(report.briefing_json)
    folder = Path(settings.briefing_artifact_dir) / report.report_date.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    for suffix, content in {
        "json": json.dumps(report.briefing_json, ensure_ascii=False, indent=2),
        "html": render_report(briefing, email=True, status=report.status),
        "txt": render_text(briefing),
    }.items():
        path = folder / f"report-{report.id}.{suffix}"
        if path.exists():
            continue
        temporary = path.with_suffix(f".{suffix}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


def tick_daily(db, *, now=None, sender=smtp_send, allow_queue=True):
    from app.services.briefing_jobs import default_analysis_input, queue_analysis

    now = now or datetime.now(UTC)
    local = now.astimezone(ZoneInfo(settings.briefing_timezone))
    if local.strftime("%H:%M") < settings.email_send_time:
        return None
    day = local.date().isoformat()
    job = db.get(DailyBriefingJob, day)
    if job is None:
        if not allow_queue:
            return None
        # Let an existing sampling run finish before queuing today's fresh daily snapshot.
        if db.scalar(select(AnalysisRun.run_id).where(
            AnalysisRun.status.in_(["queued", "running"])
        )):
            return None
        job = DailyBriefingJob(local_date=day, run_id=f"daily-{day}", status="queued")
        db.add(job)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            job = db.get(DailyBriefingJob, day)
    if job.status in {"sent", "blocked", "failed", "needs_review"}:
        return job
    row = db.get(AnalysisRun, job.run_id)
    if row is None:
        if not allow_queue:
            return job
        row = queue_analysis(db, default_analysis_input(job.run_id))
    if row.run_id != job.run_id or row.status in {"queued", "running"}:
        return job
    if row.status == "failed" or not row.report_id:
        job.status, job.error = "failed", row.error or "analysis_produced_no_report"
        db.commit()
        logger.error("daily_failed date=%s run=%s", day, job.run_id)
        return job
    report = db.get(DailyReport, row.report_id)
    job.report_id = report.id
    briefing = DailyBriefing.model_validate(report.briefing_json)
    if (report.report_date != local.date()
            or as_utc(briefing.window_end) > now
            or (now - as_utc(briefing.window_end)).total_seconds() > 6 * 3600):
        job.status, job.error = "blocked", "daily_snapshot_outside_current_window"
    elif review_briefing(briefing)["blocks_publish"]:
        job.status, job.error = "blocked", "quality_review_blocked"
    else:
        # This explicit configuration is the owner's standing authorization for daily mail.
        approve_report(db, report, actor="daily_schedule")
        job.status, job.error = "ready", None
    db.commit()
    archive_report(report)
    if job.status == "blocked" or not settings.email_schedule_enabled:
        return job
    if not email_ready():
        job.status, job.error = "awaiting_email", "smtp_not_configured"
        db.commit()
        return job
    dispatch = db.get(DailyDispatch, day)
    if dispatch is None:
        db.add(DailyDispatch(local_date=day, report_id=report.id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        dispatch = db.get(DailyDispatch, day)
    if dispatch.report_id != report.id:
        job.status, job.error = "needs_review", "another_daily_version_already_dispatched"
        db.commit()
        return job
    deliveries = send_report(db, report, sender=sender, automatic=True)
    if all(row.status == "sent" for row in deliveries):
        job.status, job.error = "sent", None
    elif any(row.status == "unknown" for row in deliveries):
        job.status, job.error = "needs_review", "smtp_delivery_unknown"
    elif any(row.attempts >= settings.email_max_attempts and row.status == "failed"
             for row in deliveries):
        job.status, job.error = "failed", "smtp_attempts_exhausted"
    else:
        job.status = "sending"
    db.commit()
    logger.info("daily_delivery date=%s run=%s report=%s status=%s",
                day, job.run_id, report.id, job.status)
    return job
