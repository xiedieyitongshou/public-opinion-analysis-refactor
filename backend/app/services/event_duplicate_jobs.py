"""Low-priority maintenance slices supervised by the existing job coordinator."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import AgentTask
from app.models.briefing import AnalysisRun, RequestLog
from app.services.briefing import as_utc
from app.services.event_duplicate_scan import TASK_TYPE, create_scan, run_slice
from app.services.request_usage import request_scope
from app.services.runtime_logging import configure_logging, redact

PREFIX = "event-dedup:"


def queued_scan(db, now):
    if not settings.event_duplicate_scan_enabled or not settings.deepseek_api_key:
        return None
    latest = db.scalar(select(AgentTask).where(
        AgentTask.task_type == TASK_TYPE,
    ).order_by(AgentTask.id.desc()))
    if latest:
        if latest.status == "queued":
            return latest
        if latest.status == "running":
            interrupt_scan(db, PREFIX + str(latest.id), "interrupted_worker")
            return latest if latest.status == "queued" else None
        if latest.status == "paused":
            if latest.retry_count < latest.max_retries:
                if now - as_utc(latest.updated_at) >= timedelta(minutes=5):
                    return latest
                return None
            latest.status, latest.finished_at = "partial", now
            db.commit()
        if now < as_utc(latest.created_at) + timedelta(hours=8):
            return None
    return create_scan(db, window_end=now,
                       max_calls=settings.event_duplicate_max_calls_per_scan,
                       token_budget=settings.event_duplicate_token_budget_per_scan)


def interrupt_scan(db, worker_id, reason):
    from app.models.briefing import JobLease
    from app.services.briefing_jobs import release_lease

    task = db.get(AgentTask, int(worker_id.removeprefix(PREFIX)))
    if task and task.status == "running":
        task.status = "queued" if task.retry_count < task.max_retries else "partial"
        task.retry_count += 1
        task.error_message = reason
        if task.status == "partial":
            task.finished_at = datetime.now(UTC)
        db.commit()
    lease = db.get(JobLease, "analysis")
    if lease and lease.owner.startswith(worker_id + ":"):
        release_lease(db, "analysis", lease.owner)


def run_worker(worker_id, factory=SessionLocal):
    from app.services.briefing_jobs import acquire_lease, release_lease

    configure_logging(worker_id.replace(":", "-"))
    owner = worker_id + ":" + uuid4().hex

    def sink(value):
        with factory() as usage_db:
            usage_db.add(RequestLog(**value))
            usage_db.commit()

    with factory() as db:
        task = db.get(AgentTask, int(worker_id.removeprefix(PREFIX)))
        if task is None or task.status not in {"queued", "paused"}:
            return
        if not acquire_lease(db, "analysis", owner, seconds=(
            settings.event_duplicate_slice_seconds + settings.llm_timeout_seconds + 30
        )):
            return
        run_id = task.input_json["run_id"]

        def collection_pending():
            return db.scalar(select(AnalysisRun.run_id).where(
                AnalysisRun.status.in_(["queued", "running"]),
            ).limit(1)) is not None

        with request_scope(run_id, {"deepseek": task.input_json["max_calls"]},
                           settings.event_duplicate_slice_seconds, sink=sink) as scope:
            try:
                run_slice(db, task, max_batches=2,
                          seconds=settings.event_duplicate_slice_seconds,
                          batch_size=10, should_yield=collection_pending)
            except Exception as exc:
                db.rollback()
                interrupt_scan(db, worker_id, redact(f"{type(exc).__name__}: {str(exc)[:160]}"))
            finally:
                scope.flush(db)
                release_lease(db, "analysis", owner)
