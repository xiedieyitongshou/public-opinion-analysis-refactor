"""Checkpoint only completed work; no source calls, semantic fallback or automatic retries."""

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import select

from app.core.config import settings
from app.models import AgentTask
from app.schemas.llm import PROMPT_VERSION

_current = ContextVar("analysis_checkpoint", default=None)


def version():
    data = [
        "llm-opt-v2-official-independent",
        PROMPT_VERSION,
        settings.deepseek_model,
        settings.deepseek_base_url,
        settings.llm_candidate_limit,
        settings.llm_retrieval_enabled,
        settings.llm_retrieval_use_embedding,
    ]
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()


@contextmanager
def task_checkpoint(db, task, *, enabled=False):
    token = _current.set((db, task) if enabled else None)
    try:
        yield
    finally:
        _current.reset(token)


def checkpoint_enabled():
    return _current.get() is not None


def load_checkpoint():
    current = _current.get()
    if current is None:
        return {}
    db, task = current
    run_id = (task.input_json or {}).get("run_id")
    if not run_id:
        return {}
    for old in db.scalars(
        select(AgentTask)
        .where(
            AgentTask.id < task.id,
            AgentTask.task_type == task.task_type,
            AgentTask.input_json["run_id"].as_string() == run_id,
        )
        .order_by(AgentTask.id.desc())
    ):
        saved = old.output_json or {}
        if (
            old.input_json == task.input_json
            and saved.get("_checkpoint", {}).get("version") == version()
        ):
            return saved
    return {}


def save_checkpoint(output, completed_ids, **metadata):
    current = _current.get()
    if current is None:
        return
    db, task = current
    value = output.model_dump(mode="json")
    value["_checkpoint"] = {
        "version": version(),
        "completed_ids": sorted(completed_ids),
        **metadata,
    }
    task.output_json = value
    db.commit()


def recover_interrupted_run(db, row, reason):
    """Collect persisted stage outputs after a killed worker, without another LLM request."""
    from app.schemas.analysis import HotspotAnalysisOutput

    plans = select(AgentTask.plan_id).where(
        AgentTask.input_json["run_id"].as_string() == row.run_id
    )
    tasks = list(
        db.scalars(select(AgentTask).where(AgentTask.plan_id.in_(plans)).order_by(AgentTask.id))
    )
    outputs = {t.task_type: t.output_json for t in tasks if t.output_json}
    collection = outputs.get("fetch_source_items")
    prepared = outputs.get("prepare_source_signals", {})
    if not collection or not prepared.get("window_end"):
        return False
    resolved = outputs.get("match_and_resolve_events", {})
    output = HotspotAnalysisOutput(
        run_id=row.run_id,
        plan_id=tasks[-1].plan_id,
        status="partial",
        window_end=prepared["window_end"],
        event_ids=resolved.get("event_ids", []),
        source_statuses={r["source_id"]: r["status"] for r in collection.get("results", [])},
        classifications=outputs.get("classify_events", {}).get("classifications", []),
        analyses=outputs.get("analyze_event_heat", {}).get("analyses", []),
        errors=[reason],
        quality_flags=["analysis_incomplete", "checkpoint_available"],
        tasks=[{"id": t.id, "tool": t.task_type, "status": t.status} for t in tasks],
    )
    row.output_json, row.collection_json = output.model_dump(mode="json"), collection
    row.window_end, row.status = output.window_end, "partial"
    return True


def event_material_version(event):
    """Invalidate a classification checkpoint after manual membership/content changes."""
    value = {
        "event_id": event.event_id,
        "title": event.title,
        "lifecycle_status": event.lifecycle_status,
        "features": (event.event_detail_json or {}).get("match_features"),
        "items": [
            (
                i.id,
                i.content_hash,
                i.summary,
                i.content,
                i.raw_metrics_json,
                i.raw_payload_json,
                str(i.published_at),
                str(i.fetched_at),
            )
            for i in sorted(event.items, key=lambda item: item.id)
        ],
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
