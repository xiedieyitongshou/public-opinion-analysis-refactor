"""Read-only 24-hour trial audit, exported alongside persisted logs and briefings."""

import argparse
import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import AgentTask, AgentToolCall
from app.models.briefing import AnalysisRun, DailyBriefingJob, EmailDelivery, RequestLog


def audit(db, since, until):
    runs = db.execute(select(
        AnalysisRun.run_id, AnalysisRun.status, AnalysisRun.started_at, AnalysisRun.finished_at,
        AnalysisRun.report_id, AnalysisRun.error,
    ).where(AnalysisRun.started_at >= since, AnalysisRun.started_at <= until)).mappings().all()
    requests = db.execute(select(RequestLog.source_id, RequestLog.status).where(
        RequestLog.started_at >= since, RequestLog.started_at <= until,
    )).all()
    by_source = {}
    for source, status in requests:
        counts = by_source.setdefault(source, {})
        counts[status] = counts.get(status, 0) + 1
    daily = db.scalars(select(DailyBriefingJob).order_by(
        DailyBriefingJob.local_date.desc()).limit(7)).all()
    deliveries = db.scalars(select(EmailDelivery).where(
        EmailDelivery.started_at >= since, EmailDelivery.started_at <= until,
    )).all()
    stale = {}
    for model in (AgentTask, AgentToolCall):
        stale[model.__tablename__] = list(db.scalars(select(model.id).where(
            model.status == "running", model.started_at < until - timedelta(hours=1),
        )))
    statuses = dict(Counter(row["status"] for row in runs))
    return {
        "since": since.isoformat(), "until": until.isoformat(), "run_counts": statuses,
        "runs": [dict(row) for row in runs], "requests": by_source,
        "daily_jobs": [{"date": row.local_date, "status": row.status,
                        "report_id": row.report_id, "error": row.error} for row in daily],
        "email_counts": dict(Counter(row.status for row in deliveries)),
        "stale_running": stale,
        "needs_attention": bool(not runs or statuses.get("failed") or statuses.get("partial")
                                or statuses.get("queued") or statuses.get("running")
                                or any(stale.values())
                                or any(row.status in {"failed", "blocked", "needs_review",
                                                      "awaiting_email"} for row in daily)
                                or any(row.status != "sent" for row in deliveries)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="ISO timestamp with timezone; default: previous 24 hours")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    until = datetime.now(UTC)
    since = datetime.fromisoformat(args.since) if args.since else until - timedelta(hours=24)
    if since.tzinfo is None:
        parser.error("--since must include a timezone")
    with SessionLocal() as db:
        result = audit(db, since, until)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                           encoding="utf-8")
    print(json.dumps({"run_counts": result["run_counts"],
                      "needs_attention": result["needs_attention"]}))


if __name__ == "__main__":
    main()
