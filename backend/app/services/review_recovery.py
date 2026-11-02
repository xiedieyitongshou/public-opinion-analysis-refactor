"""Retire technical reviews only after a later, persisted successful resolution."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import AgentTask, Event, HumanReviewTask, Item
from app.models.briefing import AnalysisRun
from app.schemas.matching import EventResolution
from app.services.review_identity import topic_identity

TECHNICAL_ERRORS = {
    "llm_unverifiable_evidence", "llm_invalid_response", "llm_timeout", "llm_network_error",
    "llm_candidate_retrieval_unavailable", "llm_input_budget_exceeded", "llm_cache_miss",
}


def _utc(value):
    if not value:
        return None
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        return None


def _observation_times(db, payload):
    """Use captured windows, not replay execution dates; include every failed round."""
    run_ids = set(payload.get("run_ids") or [])
    run_ids.add(payload.get("run_id"))
    run_ids.update(entry.get("run_id") for entry in payload.get("evidence_history") or [])
    times = [_utc(payload.get("observed_at"))]
    times += [_utc(s.get("fetched_at")) for s in payload.get("identity_sources") or []]
    for run_id in run_ids - {None}:
        run = db.get(AnalysisRun, run_id)
        if run:
            times.append(_utc(run.window_end or (run.input_json or {}).get("window_end")))
    return list(filter(None, times))


def review_observed_at(db, payload):
    return max(_observation_times(db, payload), default=None)


def _technical_only(task):
    payload = task.payload_json or {}
    features = payload.get("match_features_json") or {}
    detail = features.get("llm_detail") or {}
    category = detail.get("review_category")
    flags = [*(payload.get("guardrail_flags") or []),
             *(features.get("guardrail_flags") or [])]
    if (
        task.status != "pending" or task.review_type != "event_merge_candidate"
        or task.decision or task.reviewer
        or any("conflict" in flag for flag in flags)
        or (category and category != "technical_error")
    ):
        return False
    for entry in payload.get("evidence_history") or []:
        previous = entry.get("match_features_json") or {}
        previous_category = (previous.get("llm_detail") or {}).get("review_category")
        if (previous_category and previous_category != "technical_error") or any(
            "conflict" in flag for flag in previous.get("guardrail_flags") or []
        ):
            return False
    if category == "technical_error":
        return True
    # Older outputs lack review_category. A concrete competing target stays manual.
    return not payload.get("candidate_event_id") and bool(
        TECHNICAL_ERRORS.intersection(features.get("semantic_quality_flags") or [])
    )


def recover_technical_reviews(
    db, *, resolution, sources, run_id, observed_at, agent_task_id=None,
    accepted_at=None, human_review_id=None, dry_run=False, exclude_task_ids=(),
):
    """No commit: ownership and retirement must be saved in the same transaction.

    Merely having an owner, a high score, or an invalid LLM response is insufficient.
    The caller supplies the FINAL accepted resolution after all applicable guardrails.
    """
    if isinstance(resolution, dict):
        resolution = EventResolution.model_validate(resolution)
    if (
        resolution.action not in {"create", "merge"} or resolution.review_required
        or resolution.blocks_auto_analysis or resolution.guardrail_status == "block"
        or any("conflict" in flag for flag in resolution.match_features_json.guardrail_flags)
    ):
        return []
    sources = [s.model_dump(mode="json") if hasattr(s, "model_dump") else s for s in sources]
    try:
        item_ids = {int(s["item_id"]) for s in sources}
    except (KeyError, TypeError, ValueError):
        return []
    event = db.scalar(select(Event).where(
        Event.event_id == resolution.event_id, Event.lifecycle_status == "active",
    ))
    rows = [db.get(Item, item_id) for item_id in item_ids]
    if not event or not rows or any(
        row is None or row.event_id != event.id for row in rows
    ):
        return []
    new_keys = set(topic_identity({"identity_sources": sources}))
    observed_at = _utc(observed_at)
    accepted_at = _utc(accepted_at) or datetime.now(UTC)
    if observed_at is None:
        return []
    recovered = []
    for task in db.scalars(select(HumanReviewTask).where(
        HumanReviewTask.status == "pending",
        HumanReviewTask.review_type == "event_merge_candidate",
    ).order_by(HumanReviewTask.id)):
        if task.id == human_review_id or task.id in exclude_task_ids or not _technical_only(task):
            continue
        payload = dict(task.payload_json or {})
        try:
            old_ids = {int(v) for v in payload.get("item_ids", [])}
        except (ValueError, TypeError):
            continue
        identity = dict(payload)
        old_rows = None
        if not (identity.get("identity_sources") or identity.get("source_citations")):
            old_rows = [db.get(Item, item_id) for item_id in old_ids]
            identity["identity_sources"] = [
                {"url": row.url, "title": row.title, "source_type": row.source.source_type}
                for row in old_rows if row is not None
            ]
        old_keys = set(topic_identity(identity))
        strong_identity = old_keys == new_keys and not any(
            key.startswith("signal:") for key in old_keys
        )
        if not (old_ids and old_ids.issubset(item_ids)) and not strong_identity:
            continue
        old_rows = old_rows if old_rows is not None else [db.get(Item, i) for i in old_ids]
        if any(row is None or row.event_id not in {None, event.id} for row in old_rows):
            continue
        times = _observation_times(db, payload)
        failed_at = max(times, default=None)
        if failed_at is None or observed_at < failed_at:
            continue
        if observed_at == failed_at:
            if agent_task_id and task.agent_task_id:
                later_attempt = agent_task_id > task.agent_task_id
            else:
                # SQLite legacy timestamps have second precision. Avoid false ordering.
                later_attempt = human_review_id is not None or (
                    task.created_at
                    and accepted_at > _utc(task.created_at) + timedelta(seconds=1)
                )
            if not later_attempt:
                continue
        weak_identity = any(key.startswith("weibo:") for key in old_keys) or any(
            row.source.platform == "weibo" for row in old_rows
        )
        if weak_identity and (
            observed_at - min(times) > timedelta(hours=24)
        ):
            continue
        audit = {
            "status": "recovered", "version": 1, "run_id": run_id,
            "event_id": event.event_id, "event_signal_id": resolution.event_signal_id,
            "action": resolution.action, "item_ids": sorted(item_ids),
            "source_signal_ids": sorted({s.get("source_signal_id") for s in sources}
                                        - {None}),
            "observed_at": observed_at.isoformat(), "accepted_at": accepted_at.isoformat(),
            "agent_task_id": agent_task_id, "human_review_id": human_review_id,
            "basis": "human_decision" if human_review_id else "accepted_resolution",
            "review_id": task.id,
        }
        recovered.append(audit)
        if dry_run:
            continue
        task.payload_json = {**payload, "automatic_recovery": audit}
        task.status = "cancelled"
        task.decision = "auto_recovered"
        task.reviewer = "system:later-success"
        task.decision_reason = "后续已成功处理该话题，原技术异常已恢复，无需重复人工复核。"
        task.resolved_at = datetime.now(UTC)
    return recovered


def recover_saved_technical_reviews(db, *, dry_run=True):
    """Explicit, local backfill from saved accepted outputs; never call an external API."""
    recovered = {}
    for task in db.scalars(select(AgentTask).where(
        AgentTask.task_type == "match_and_resolve_events", AgentTask.output_json.is_not(None),
    ).order_by(AgentTask.id.desc())):
        data, output = task.input_json or {}, task.output_json or {}
        sources = {s["source_signal_id"]: s for s in data.get("source_signals", [])}
        signals = {s["event_signal_id"]: s for s in data.get("event_signals", [])}
        for resolution in output.get("event_resolutions", []):
            signal = signals.get(resolution.get("event_signal_id"), {})
            ids = signal.get("source_signal_ids") or []
            if not ids or any(key not in sources for key in ids):
                continue
            for audit in recover_technical_reviews(
                db, resolution=resolution, sources=[sources[key] for key in ids],
                run_id=data.get("run_id"), observed_at=data.get("window_end"),
                agent_task_id=task.id, accepted_at=task.finished_at or task.created_at,
                dry_run=dry_run,
            ):
                recovered.setdefault(audit["review_id"], audit)
    return list(recovered.values())
