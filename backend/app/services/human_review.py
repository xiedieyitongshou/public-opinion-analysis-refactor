"""Human review queue service for Day 41."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event, GuardrailViolation, HumanReviewTask
from app.schemas import (
    CreateHumanReviewTaskInput,
    CreateHumanReviewTaskOutput,
    HumanReviewDecisionInput,
    HumanReviewDecisionOutput,
    HumanReviewTaskRecord,
)


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

    created_count = 0
    updated_count = 0
    records: list[HumanReviewTaskRecord] = []
    for candidate_review in candidate_reviews:
        payload = _review_payload(input_data, candidate_review)
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
    if task.status != "pending":
        return HumanReviewDecisionOutput(
            status="failed",
            human_review_task=task_record(task),
            message=f"Human review task is not pending: {task.status}",
        )

    payload = dict(task.payload_json or {})
    now = datetime.now(UTC)
    event_id = _apply_decision_side_effect(db, payload, input_data, now=now)
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
    target_key = payload.get("idempotency_key")
    rows = db.scalars(
        select(HumanReviewTask).where(
            HumanReviewTask.review_type == review_type,
            HumanReviewTask.status == "pending",
        )
    ).all()
    for row in rows:
        if (row.payload_json or {}).get("idempotency_key") == target_key:
            return row
    return None


def _idempotency_key(review_type: str, payload: dict[str, Any]) -> str:
    raw = "|".join(
        [
            review_type,
            str(payload.get("event_signal_id") or ""),
            str(payload.get("candidate_event_id") or ""),
            str(payload.get("run_id") or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _apply_decision_side_effect(
    db: Session,
    payload: dict[str, Any],
    input_data: HumanReviewDecisionInput,
    *,
    now: datetime,
) -> str | None:
    candidate_event_id = payload.get("candidate_event_id")
    if input_data.decision == "merge_to_existing":
        if candidate_event_id:
            _append_event_review_detail(db, str(candidate_event_id), payload, input_data, now=now)
        return str(candidate_event_id) if candidate_event_id else None
    if input_data.decision == "create_new_event":
        event_id = _new_event_id(payload, now)
        event = Event(
            event_id=event_id,
            title=input_data.event_title or _event_title(payload, event_id),
            event_type=None,
            lifecycle_status="active",
            confidence_score=payload.get("confidence"),
            first_seen_at=now,
            last_seen_at=now,
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
    event.last_seen_at = now
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
        same_signal = event_signal_id and row.event_signal_id == event_signal_id
        same_event = candidate_event_id and row.event_id == candidate_event_id
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


def _event_title(payload: dict[str, Any], event_id: str) -> str:
    citations = payload.get("source_citations") or []
    if citations and isinstance(citations[0], dict) and citations[0].get("title"):
        return str(citations[0]["title"])
    return f"Human reviewed event {event_id}"
