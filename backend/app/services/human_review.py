"""Human review queue service for Day 41."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, GuardrailViolation, HumanReviewTask, Item
from app.schemas import (
    CreateHumanReviewTaskInput,
    CreateHumanReviewTaskOutput,
    HumanReviewDecisionInput,
    HumanReviewDecisionOutput,
    HumanReviewTaskRecord,
)
from app.services.review_identity import merge_evidence, relation_key, topic_identity


def create_human_review_tasks(
    db: Session,
    input_data: CreateHumanReviewTaskInput,
    *,
    agent_task_id: int | None = None,
) -> CreateHumanReviewTaskOutput:
    candidate_reviews = _candidate_reviews(input_data)
    if not candidate_reviews:
        return CreateHumanReviewTaskOutput(
            status="skipped",
            message="No candidate_review payloads were provided.",
        )

    consolidate_pending_reviews(db)
    created_count = 0
    updated_count = 0
    records: list[HumanReviewTaskRecord] = []
    for candidate_review in candidate_reviews:
        payload = _review_payload(input_data, candidate_review)
        payload = identity_payload(db, payload)
        existing = _find_pending_duplicate(db, input_data.review_type, payload)
        if existing is None:
            task = HumanReviewTask(
                agent_task_id=agent_task_id,
                review_type=input_data.review_type,
                status="pending",
                priority=input_data.priority,
                payload_json=payload,
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            created_count += 1
        else:
            task = existing
            old = task.payload_json or {}
            payload = merge_evidence(old, payload)
            payload["run_id"] = old.get("run_id") or payload.get("run_id")
            task.payload_json = payload
            task.priority = input_data.priority
            db.add(task)
            db.commit()
            db.refresh(task)
            updated_count += 1
        records.append(task_record(task))

    return CreateHumanReviewTaskOutput(
        status="succeeded",
        created_count=created_count,
        updated_count=updated_count,
        skipped_count=0,
        human_review_tasks=records,
    )


def decide_human_review_task(
    db: Session,
    task: HumanReviewTask,
    input_data: HumanReviewDecisionInput,
) -> HumanReviewDecisionOutput:
    from uuid import uuid4

    from app.services.briefing_jobs import acquire_lease, release_lease
    from app.services.review_refresh import refresh_after_review

    owner = str(uuid4())
    if not acquire_lease(db, "analysis", owner, seconds=900):
        return HumanReviewDecisionOutput(
            status="failed",
            human_review_task=task_record(task),
            message="采集分析正在运行，请完成后再处理候选。",
        )
    try:
        result = _decide_human_review_task(db, task, input_data)
        if result.status == "succeeded":
            try:
                result.report_id = refresh_after_review(db, task, result.event_id)
                result.refresh_status = "refreshed" if result.report_id else "no_analysis_run"
            except Exception as exc:
                db.rollback()
                result.refresh_status = "failed"
                result.message = f"审核已保存，重算失败：{type(exc).__name__}"
            payload = dict(task.payload_json or {})
            payload["refresh"] = {
                "status": result.refresh_status,
                "report_id": result.report_id,
                "error": result.message,
            }
            task.payload_json = payload
            db.commit()
        return result
    finally:
        release_lease(db, "analysis", owner)


def _decide_human_review_task(
    db: Session,
    task: HumanReviewTask,
    input_data: HumanReviewDecisionInput,
) -> HumanReviewDecisionOutput:
    if task.status != "pending":
        return HumanReviewDecisionOutput(
            status="failed",
            human_review_task=task_record(task),
            message=f"Human review task is not pending: {task.status}",
        )

    consolidate_pending_reviews(db, preferred_id=task.id)
    payload = dict(task.payload_json or {})
    now = datetime.now(UTC)
    try:
        event_id = _apply_decision_side_effect(db, payload, input_data, now=now)
        if event_id:
            _associate_review_items(db, event_id, payload)
    except ValueError as exc:
        db.rollback()
        return HumanReviewDecisionOutput(
            status="failed",
            human_review_task=task_record(task),
            message=str(exc),
        )
    task.status = "ignored" if input_data.decision == "ignore" else "resolved"
    task.reviewer = input_data.reviewer
    task.decision = input_data.decision
    task.decision_reason = input_data.decision_reason
    task.resolved_at = now
    payload["decision_audit"] = {
        "decision": input_data.decision,
        "reviewer": input_data.reviewer,
        "decision_reason": input_data.decision_reason,
        "resolved_at": now.isoformat(),
        "event_id": event_id,
    }
    task.payload_json = payload
    db.add(task)
    _resolve_related_guardrail_violations(db, payload, now=now)
    db.commit()
    db.refresh(task)
    return HumanReviewDecisionOutput(
        status="succeeded",
        human_review_task=task_record(task),
        event_id=event_id,
    )


def task_record(task: HumanReviewTask) -> HumanReviewTaskRecord:
    return HumanReviewTaskRecord(
        id=task.id,
        review_type=task.review_type,
        status=task.status,
        priority=task.priority,
        payload=task.payload_json or {},
        reviewer=task.reviewer,
        decision=task.decision,
        decision_reason=task.decision_reason,
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
        resolved_at=task.resolved_at.isoformat() if task.resolved_at else None,
    )


def _candidate_reviews(input_data: CreateHumanReviewTaskInput) -> list[dict[str, Any]]:
    reviews = [review.model_dump(mode="json") for review in input_data.candidate_reviews]
    if input_data.candidate_review is not None:
        reviews.insert(0, input_data.candidate_review.model_dump(mode="json"))
    payload_candidate = input_data.payload.get("candidate_review")
    if isinstance(payload_candidate, dict):
        reviews.append(payload_candidate)
    payload_candidates = input_data.payload.get("candidate_reviews")
    if isinstance(payload_candidates, list):
        reviews.extend(item for item in payload_candidates if isinstance(item, dict))
    for resolution in input_data.payload.get("event_resolutions", []):
        if isinstance(resolution, dict) and isinstance(resolution.get("candidate_review"), dict):
            reviews.append(resolution["candidate_review"])
    return reviews


def _review_payload(
    input_data: CreateHumanReviewTaskInput,
    candidate_review: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(input_data.payload)
    sources = payload.pop("source_signals_by_id", {})
    signals = payload.pop("event_signals_by_id", {})
    signal = signals.get(candidate_review.get("event_signal_id"))
    if signal:
        payload["event_signal"] = signal
        payload["title"] = signal.get("title")
    if sources:
        related = [
            sources[key] for key in candidate_review.get("source_signal_ids", []) if key in sources
        ]
        payload["item_ids"] = [source["item_id"] for source in related if source.get("item_id")]
        payload["identity_sources"] = [
            {k: source.get(k) for k in ("url", "title", "source_type", "fetched_at")}
            for source in related
        ]
        payload["source_citations"] = [
            source.get("classification_features", {}).get("source_citation", {})
            for source in related
        ]
    payload.pop("event_resolutions", None)
    payload.pop("candidate_review", None)
    payload.pop("candidate_reviews", None)
    payload.update(candidate_review)
    payload["review_type"] = input_data.review_type
    payload["run_id"] = input_data.run_id or payload.get("run_id")
    payload["source_citations"] = input_data.source_citations or payload.get("source_citations", [])
    payload["idempotency_key"] = _idempotency_key(input_data.review_type, payload)
    return payload


def _find_pending_duplicate(
    db: Session,
    review_type: str,
    payload: dict[str, Any],
) -> HumanReviewTask | None:
    rows = db.scalars(
        select(HumanReviewTask).where(
            HumanReviewTask.review_type == review_type,
            HumanReviewTask.status == "pending",
        )
    ).all()
    for row in rows:
        if same_pending_relation(db, review_type, row.payload_json or {}, payload):
            return row
    return None


def _idempotency_key(review_type: str, payload: dict[str, Any]) -> str:
    return relation_key(review_type, payload)


def identity_payload(db, payload):
    """Hydrate old rows without changing their original per-run identifiers."""
    payload = dict(payload)
    if not payload.get("identity_sources") and payload.get("item_ids"):
        rows = db.scalars(select(Item).where(Item.id.in_(payload["item_ids"])))
        payload["identity_sources"] = [
            {"url": row.url, "title": row.title, "source_type": row.source.source_type}
            for row in rows
        ]
    payload["topic_identity"] = topic_identity(payload)
    payload["identity_version"] = 1
    return payload


def _within_topic_window(db, left, right):
    if not any(k.startswith("weibo:") for k in topic_identity(left)):
        return True
    from app.models.briefing import AnalysisRun
    from app.services.briefing import as_utc

    times = []
    for p in (left, right):
        run = db.get(AnalysisRun, p.get("run_id")) if p.get("run_id") else None
        value = run.started_at if run else p.get("observed_at")
        if not value:
            return left.get("run_id") == right.get("run_id")
        times.append(as_utc(value))
    return abs((times[1] - times[0]).total_seconds()) <= 24 * 3600


def same_pending_relation(db, review_type, left, right):
    left, right = identity_payload(db, left), identity_payload(db, right)
    return (relation_key(review_type, left) == relation_key(review_type, right)
            and _within_topic_window(db, left, right))


def consolidate_pending_reviews(db, preferred_id=None):
    """Archive duplicate queue entries; keep original evidence and canonical task linkage."""
    rows = list(db.scalars(select(HumanReviewTask).where(
        HumanReviewTask.status == "pending",
        HumanReviewTask.review_type == "event_merge_candidate",
    ).order_by(HumanReviewTask.id)))
    if preferred_id is not None:
        rows.sort(key=lambda row: (row.id != preferred_id, row.id))
    groups = []
    archived = 0
    for row in rows:
        payload = identity_payload(db, row.payload_json or {})
        for canonical in groups:
            if not same_pending_relation(db, row.review_type, canonical.payload_json, payload):
                continue
            combined = merge_evidence(canonical.payload_json, payload)
            # Preserve the first observation as the anchor for the weak topic's 24h scope.
            combined["run_id"] = canonical.payload_json.get("run_id")
            combined["grouped_review_ids"] = list(dict.fromkeys(
                [*canonical.payload_json.get("grouped_review_ids", []), row.id]
            ))
            canonical.payload_json = combined
            row.payload_json = {**payload, "grouped_into_review_id": canonical.id}
            row.status = "cancelled"
            archived += 1
            break
        else:
            row.payload_json = payload
            groups.append(row)
    db.flush()
    return archived


def reuse_reviewed_merge(db, signal, resolution, sources=None, run_id=None):
    """Reuse explicit decisions for the same relation, unless new facts conflict."""
    if resolution.action not in {"candidate_review", "merge"} or any(
        flag.endswith("_conflict") for flag in resolution.match_features_json.guardrail_flags
    ):
        return resolution
    rows = db.scalars(
        select(HumanReviewTask)
        .where(
            HumanReviewTask.status.in_(["resolved", "ignored"]),
            HumanReviewTask.decision.in_(["merge_to_existing", "reject"]),
        )
        .order_by(HumanReviewTask.id.desc())
    )
    for row in rows:
        payload = identity_payload(db, row.payload_json or {})
        current = {
            "event_signal_id": signal.event_signal_id,
            "identity_sources": sources or [], "run_id": run_id,
        }
        if (
            topic_identity(payload) == topic_identity(current)
            and payload.get("candidate_event_id") == resolution.event_id
            and _within_topic_window(db, payload, current)
        ):
            return resolution.model_copy(
                update={
                    "action": "merge" if row.decision == "merge_to_existing" else "reject",
                    "confidence": 0.98,
                    "review_required": False,
                    "blocks_auto_analysis": False,
                    "blocks_publish": False,
                    "guardrail_status": "pass",
                    "candidate_review": None,
                    "reason": f"reused explicit human {row.decision} decision {row.id}",
                }
            )
    return resolution


def _apply_decision_side_effect(
    db: Session,
    payload: dict[str, Any],
    input_data: HumanReviewDecisionInput,
    *,
    now: datetime,
) -> str | None:
    candidate_event_id = payload.get("candidate_event_id")
    if input_data.decision == "merge_to_existing":
        if (
            not candidate_event_id
            or db.scalar(select(Event).where(Event.event_id == str(candidate_event_id))) is None
        ):
            raise ValueError("The target event for this review does not exist")
        _append_event_review_detail(db, str(candidate_event_id), payload, input_data, now=now)
        return str(candidate_event_id)
    if input_data.decision == "create_new_event":
        item_ids = [int(value) for value in payload.get("item_ids", [])]
        rows = db.scalars(select(Item).where(Item.id.in_(item_ids))).all() if item_ids else []
        if rows and all(row.signal_role == "search_enrichment_signal" for row in rows):
            raise ValueError("搜索增强样本不能独立创建自动发现热点，请关联已有父事件")
        observed = _original_observed_times(db, payload)
        event_id = _new_event_id(payload, now)
        event = Event(
            event_id=event_id,
            title=(input_data.event_title or "").strip() or _event_title(payload, event_id),
            event_type=None,
            lifecycle_status="active",
            confidence_score=payload.get("confidence"),
            first_seen_at=min(observed) if observed else None,
            last_seen_at=max(observed) if observed else None,
            keywords_json=[],
            source_citations_json=payload.get("source_citations") or [],
            event_detail_json={
                "created_from_human_review": True,
                "event_signal_id": payload.get("event_signal_id"),
                "source_signal_ids": payload.get("source_signal_ids", []),
                "review_decision": input_data.decision,
                "decision_reason": input_data.decision_reason,
            },
        )
        db.add(event)
        db.flush()
        return event_id
    return None


def _associate_review_items(db: Session, event_id: str, payload: dict[str, Any]) -> None:
    """Make a confirmed create/merge visible to the next scoring round."""
    item_ids = payload.get("item_ids") or []
    if not item_ids:
        return
    event = db.scalar(select(Event).where(Event.event_id == event_id))
    rows = list(db.scalars(select(Item).where(Item.id.in_([int(value) for value in item_ids]))))
    if len(rows) != len(set(item_ids)):
        raise ValueError("A reviewed source item no longer exists")
    citations = {str(value.get("item_id")): value for value in event.source_citations_json or []}
    for row in rows:
        if row.event_id not in {None, event.id}:
            raise ValueError("A reviewed item already belongs to a different event")
        row.event_id = event.id
        if row.source_citation_json:
            citations[str(row.id)] = row.source_citation_json
    event.source_citations_json = list(citations.values())
    detail = dict(event.event_detail_json or {})
    detail["source_signal_ids"] = list(
        dict.fromkeys([*detail.get("source_signal_ids", []), *payload.get("source_signal_ids", [])])
    )
    event.event_detail_json = detail


def _append_event_review_detail(
    db: Session,
    stable_event_id: str,
    payload: dict[str, Any],
    input_data: HumanReviewDecisionInput,
    *,
    now: datetime,
) -> None:
    event = db.scalar(select(Event).where(Event.event_id == stable_event_id))
    if event is None:
        return
    detail = dict(event.event_detail_json or {})
    reviews = list(detail.get("human_review_decisions", []))
    reviews.append(
        {
            "event_signal_id": payload.get("event_signal_id"),
            "source_signal_ids": payload.get("source_signal_ids", []),
            "decision": input_data.decision,
            "decision_reason": input_data.decision_reason,
            "reviewed_at": now.isoformat(),
        }
    )
    detail["human_review_decisions"] = reviews
    event.event_detail_json = detail
    observed = _original_observed_times(db, payload)
    if observed:
        from app.services.briefing import as_utc

        event.last_seen_at = max(
            [*observed, *([as_utc(event.last_seen_at)] if event.last_seen_at else [])]
        )
    db.add(event)


def _resolve_related_guardrail_violations(
    db: Session,
    payload: dict[str, Any],
    *,
    now: datetime,
) -> None:
    event_signal_id = payload.get("event_signal_id")
    candidate_event_id = payload.get("candidate_event_id")
    rows = db.scalars(
        select(GuardrailViolation).where(GuardrailViolation.resolved.is_(False))
    ).all()
    for row in rows:
        signal_ids = {*payload.get("event_signal_ids", []), event_signal_id} - {None}
        same_signal = row.event_signal_id in signal_ids
        same_event = not signal_ids and candidate_event_id and row.event_id == candidate_event_id
        if same_signal or same_event:
            row.resolved = True
            row.resolved_at = now
            db.add(row)


def _new_event_id(payload: dict[str, Any], now: datetime) -> str:
    seed = "|".join(
        [
            str(payload.get("event_signal_id") or ""),
            str(payload.get("run_id") or ""),
            str(payload.get("confidence") or ""),
        ]
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
    return f"evt_{now.date().strftime('%Y%m%d')}_{digest}"


def _original_observed_times(db, payload):
    from app.services.briefing import as_utc

    values = [
        as_utc(citation["fetched_at"])
        for citation in payload.get("source_citations", [])
        if citation.get("fetched_at")
    ]
    if not values:
        rows = db.scalars(
            select(Item).where(Item.id.in_([int(value) for value in payload.get("item_ids", [])]))
        )
        values = [as_utc(row.fetched_at) for row in rows if row.fetched_at]
    return values


def _event_title(payload: dict[str, Any], event_id: str) -> str:
    for value in (payload.get("title"), (payload.get("event_signal") or {}).get("title")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    citations = payload.get("source_citations") or []
    for citation in citations:
        title = citation.get("title") if isinstance(citation, dict) else None
        if isinstance(title, str) and title.strip():
            return title.strip()
    return "待命名事件"
