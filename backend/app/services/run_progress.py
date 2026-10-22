"""Small atomic progress snapshots, independent of long business transactions."""

import hashlib
import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.models.briefing import AnalysisRun, RequestLog

_current = ContextVar("run_progress", default=None)


def progress_path(bind, run_id):
    filename = bind.url.database
    if bind.dialect.name != "sqlite" or not filename or filename == ":memory:":
        return None
    name = hashlib.sha256(run_id.encode()).hexdigest() + ".json"
    return Path(filename).resolve().parent / "progress" / name


@contextmanager
def progress_scope(bind, run_id):
    value = {"path": progress_path(bind, run_id), "state": {"sources": {}, "run_id": run_id}}
    token = _current.set(value)
    try:
        yield
    finally:
        _current.reset(token)


def emit_progress(*, stage=None, source=None, **values):
    current = _current.get()
    if current is None:
        return
    state = current["state"]
    now = datetime.now(UTC).isoformat()
    state["updated_at"] = now
    if stage:
        state["stage"] = stage
    if source:
        row = state["sources"].setdefault(source, {"source_id": source, "started_at": now})
        row.update(values, updated_at=now)
    path = current["path"]
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            logging.getLogger(__name__).warning("progress_snapshot_unavailable")


def current_progress(db):
    # Active run wins over older finished reports or summary runs.
    row = db.scalar(select(AnalysisRun).where(
        AnalysisRun.status.in_(["queued", "running"])
    ).order_by(AnalysisRun.started_at.desc()))
    if row is None:
        row = db.scalar(select(AnalysisRun).where(
            AnalysisRun.input_json["report_window"].as_string().is_(None)
        ).order_by(AnalysisRun.started_at.desc()))
    if row is None:
        return {"status": "idle", "sources": [], "requests": []}
    state = {}
    path = progress_path(db.get_bind(), row.run_id)
    if path and path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    sources = state.get("sources", {})
    for source in (row.input_json.get("collection") or {}).get("source_ids", []):
        sources.setdefault(source, {"source_id": source, "status": "waiting"})
    for result in (row.collection_json or {}).get("results", []):
        source = result["source_id"]
        sources.setdefault(source, {"source_id": source}).update(
            status=result["status"], returned_count=result.get("returned_count"),
            error=result.get("error_message"), duration_ms=result.get("duration_ms"),
        )
    active = row.status in {"queued", "running"}
    if not active:
        for source in sources.values():
            if source.get("status") in {"waiting", "running"}:
                source["status"] = "interrupted" if row.status == "failed" else "not_observed"
    grouped = {}
    for request in db.scalars(select(RequestLog).where(RequestLog.run_id == row.run_id)):
        group = grouped.setdefault(request.source_id, {
            "source_id": request.source_id, "count": 0, "failed": 0, "duration_ms": 0,
        })
        group["count"] += 1
        group["failed"] += request.status != "succeeded"
        group["duration_ms"] += request.duration_ms
    started = row.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    end = row.finished_at or datetime.now(UTC)
    end = end.replace(tzinfo=UTC) if end.tzinfo is None else end
    return {
        "run_id": row.run_id, "status": row.status, "started_at": started,
        "elapsed_seconds": max(0, int((end - started).total_seconds())),
        "stage": state.get("stage") if active else row.status,
        "updated_at": state.get("updated_at"), "error": row.error,
        "report_id": row.report_id, "sources": list(sources.values()),
        "requests": list(grouped.values()),
    }
