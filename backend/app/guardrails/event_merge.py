"""Event merge guardrail rules and final resolution decisions."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.guardrails.registry import GuardrailRegistry
from app.schemas import (
    EventMatchConfig,
    EventMergeCandidateReview,
    EventResolution,
    GuardrailCheckInput,
    GuardrailCheckResult,
    GuardrailRunResult,
    MatchFeatures,
)


def apply_merge_guardrails(
    resolution: EventResolution,
    *,
    event_signal: dict[str, Any] | None = None,
    match_config: EventMatchConfig | None = None,
    registry: GuardrailRegistry,
    db: Session | None = None,
    agent_task_id: int | None = None,
    tool_call_id: int | None = None,
) -> EventResolution:
    """Apply registered event-merge rules and return the final resolution."""

    config = match_config or EventMatchConfig()
    payload = {
        "event_resolution": resolution.model_dump(mode="json"),
        "event_signal": event_signal or {},
        "match_config": config.model_dump(mode="json"),
    }
    result = registry.run(
        GuardrailCheckInput(
            payload=payload,
            subject_type="event_merge",
            subject_id=resolution.event_id,
            agent_task_id=agent_task_id,
            tool_call_id=tool_call_id,
        ),
        rule_types={"event_merge"},
        db=db,
    )
    return finalize_event_resolution(resolution, result, event_signal=event_signal or {})


def finalize_event_resolution(
    resolution: EventResolution,
    guardrail_result: GuardrailRunResult,
    *,
    event_signal: dict[str, Any],
) -> EventResolution:
    features = resolution.match_features_json.model_copy(
        update={
            "guardrail_flags": _dedupe(
                [
                    *resolution.match_features_json.guardrail_flags,
                    *[
                        flag
                        for violation in guardrail_result.violations
                        for flag in violation.evidence.get("guardrail_flags", [])
                    ],
                ]
            )
        }
    )
    action = resolution.action
    reason = resolution.reason
    review_required = resolution.review_required or guardrail_result.review_required

    if guardrail_result.status == "block":
        action = "reject"
        reason = _join_reason("blocked by event merge guardrails", guardrail_result)
        review_required = False
    elif guardrail_result.status == "warn":
        reason = _join_reason("downgraded by event merge guardrails", guardrail_result)
        if action == "merge":
            action = "candidate_review"
            review_required = True

    candidate_review = None
    if action == "candidate_review" or review_required:
        candidate_review = EventMergeCandidateReview(
            event_signal_id=resolution.event_signal_id,
            candidate_event_id=resolution.matched_candidate_event_id,
            recommended_action=resolution.action,
            confidence=resolution.confidence,
            reason=reason,
            matched_by=resolution.matched_by,
            match_features_json=features,
            guardrail_flags=features.guardrail_flags,
            source_signal_ids=[
                item for item in event_signal.get("source_signal_ids", []) if isinstance(item, str)
            ],
        )

    return resolution.model_copy(
        update={
            "action": action,
            "reason": reason,
            "match_features_json": features,
            "guardrail_status": guardrail_result.status,
            "review_required": review_required,
            "blocks_auto_analysis": False,
            "blocks_publish": guardrail_result.blocks_publish,
            "candidate_review": candidate_review,
        }
    )


def check_event_merge_low_confidence(input_data: GuardrailCheckInput) -> GuardrailCheckResult:
    resolution = _resolution(input_data.payload)
    config = _config(input_data.payload)
    if (
        resolution.get("matched_candidate_event_id")
        and resolution.get("confidence", 0.0) < config.auto_merge_confidence_threshold
    ):
        return _event_merge_result(
            "event_merge.low_confidence",
            "warn",
            "Low confidence match cannot be auto-merged.",
            input_data,
            ["low_confidence"],
            review_required=resolution.get("confidence", 0.0)
            >= config.candidate_review_confidence_threshold,
        )
    return _pass("event_merge.low_confidence", input_data)


def check_event_merge_entity_conflict(input_data: GuardrailCheckInput) -> GuardrailCheckResult:
    if _has_flag(input_data.payload, "entity_conflict"):
        return _event_merge_result(
            "event_merge.entity_conflict",
            "block",
            "Entity conflict blocks event merge.",
            input_data,
            ["entity_conflict"],
        )
    return _pass("event_merge.entity_conflict", input_data)


def check_event_merge_time_conflict(input_data: GuardrailCheckInput) -> GuardrailCheckResult:
    if _has_flag(input_data.payload, "time_conflict"):
        return _event_merge_result(
            "event_merge.time_conflict",
            "block",
            "Time window conflict blocks event merge.",
            input_data,
            ["time_conflict"],
        )
    return _pass("event_merge.time_conflict", input_data)


def check_event_merge_embedding_conflict(input_data: GuardrailCheckInput) -> GuardrailCheckResult:
    features = _features(input_data.payload)
    config = _config(input_data.payload)
    if (
        features.embedding_similarity is not None
        and features.embedding_similarity >= config.embedding_auto_merge_min_similarity
        and (
            _has_flag(input_data.payload, "entity_conflict")
            or _has_flag(input_data.payload, "time_conflict")
        )
    ):
        return _event_merge_result(
            "event_merge.embedding_conflict",
            "block",
            "Embedding similarity conflicts with entity or time hard constraints.",
            input_data,
            ["embedding_conflict", "semantic_false_positive_risk"],
        )
    return _pass("event_merge.embedding_conflict", input_data)


def check_event_merge_embedding_only(input_data: GuardrailCheckInput) -> GuardrailCheckResult:
    features = _features(input_data.payload)
    config = _config(input_data.payload)
    embedding_only = (
        features.embedding_similarity is not None
        and features.embedding_similarity >= config.embedding_auto_merge_min_similarity
        and "embedding_rerank" in features.matched_by
        and not features.hard_constraints_passed
    )
    if embedding_only or _has_flag(input_data.payload, "embedding_only_without_hard_constraint"):
        return _event_merge_result(
            "event_merge.embedding_only",
            "warn",
            "Embedding-only match lacks hard constraint support and requires review.",
            input_data,
            ["embedding_only_without_hard_constraint"],
            review_required=True,
        )
    return _pass("event_merge.embedding_only", input_data)


def check_event_merge_weak_signal_only(input_data: GuardrailCheckInput) -> GuardrailCheckResult:
    signal = _signal(input_data.payload)
    features = _features(input_data.payload)
    if signal.get("is_weak_signal") and not (
        features.id_match
        or features.url_match
        or features.entity_overlap > 0
        or features.action_overlap > 0
    ):
        return _event_merge_result(
            "event_merge.weak_signal_only",
            "warn",
            "Weak community signal cannot be published as a confirmed factual event.",
            input_data,
            ["weak_signal_only"],
            review_required=True,
        )
    return _pass("event_merge.weak_signal_only", input_data)


def _event_merge_result(
    rule_name: str,
    severity: str,
    message: str,
    input_data: GuardrailCheckInput,
    guardrail_flags: list[str],
    *,
    review_required: bool = False,
) -> GuardrailCheckResult:
    evidence = _base_evidence(input_data.payload)
    evidence["guardrail_flags"] = _dedupe(
        [*evidence.get("guardrail_flags", []), *guardrail_flags]
    )
    return GuardrailCheckResult(
        rule_name=rule_name,
        rule_type="event_merge",
        severity=severity,
        passed=severity == "pass",
        message=message,
        subject_type=input_data.subject_type,
        subject_id=input_data.subject_id,
        evidence=evidence,
        review_required=review_required,
        blocks_publish=severity == "block",
    )


def _pass(rule_name: str, input_data: GuardrailCheckInput) -> GuardrailCheckResult:
    return GuardrailCheckResult(
        rule_name=rule_name,
        rule_type="event_merge",
        severity="pass",
        passed=True,
        message="Guardrail passed.",
        subject_type=input_data.subject_type,
        subject_id=input_data.subject_id,
        evidence=_base_evidence(input_data.payload),
    )


def _base_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    resolution = _resolution(payload)
    signal = _signal(payload)
    features = _features(payload)
    return {
        "event_signal_id": resolution.get("event_signal_id"),
        "event_id": resolution.get("event_id"),
        "confidence": resolution.get("confidence"),
        "matched_by": resolution.get("matched_by", []),
        "match_features_json": features.model_dump(mode="json"),
        "guardrail_flags": list(features.guardrail_flags),
        "source_signal_ids": signal.get("source_signal_ids", []),
    }


def _resolution(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("event_resolution")
    return value if isinstance(value, dict) else {}


def _signal(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("event_signal")
    return value if isinstance(value, dict) else {}


def _features(payload: dict[str, Any]) -> MatchFeatures:
    raw = _resolution(payload).get("match_features_json") or {}
    if isinstance(raw, MatchFeatures):
        return raw
    return MatchFeatures.model_validate(raw)


def _config(payload: dict[str, Any]) -> EventMatchConfig:
    raw = payload.get("match_config") or {}
    if isinstance(raw, EventMatchConfig):
        return raw
    return EventMatchConfig.model_validate(raw)


def _has_flag(payload: dict[str, Any], flag: str) -> bool:
    return flag in _features(payload).guardrail_flags


def _join_reason(prefix: str, guardrail_result: GuardrailRunResult) -> str:
    names = [violation.rule_name for violation in guardrail_result.violations]
    return f"{prefix}: {', '.join(names)}" if names else prefix


def _dedupe(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[Any] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
