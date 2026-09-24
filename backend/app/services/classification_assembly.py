"""Day 46 hotspot classification assembly from platform presence and support evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.models import Event, PlatformScore
from app.schemas import (
    CrossPlatformMatchType,
    EventResolution,
    EvidenceSummary,
    HotspotClassificationAssembly,
    HotspotClassificationInput,
    OfficialSupportResult,
    PlatformPresence,
    SourceSignal,
)
from app.schemas.display import OfficialSupportStatus
from app.services.event_classification import classify_hotspot

WINDOW_HOURS = 24


def assemble_hotspot_classification(
    *,
    run_id: str,
    event: Event,
    platform_scores: list[PlatformScore],
    source_signals: list[SourceSignal] | None = None,
    official_support_result: OfficialSupportResult | None = None,
    event_resolution: EventResolution | None = None,
    window_end: datetime | None = None,
    persist: bool = True,
) -> HotspotClassificationAssembly:
    """Assemble and optionally persist the Day 46 classification for one event."""

    effective_window_end = window_end or datetime.now(UTC)
    window_start = effective_window_end - timedelta(hours=WINDOW_HOURS)
    current_scores = [
        score
        for score in platform_scores
        if _in_window(_datetime_value(score.calculated_at), window_start, effective_window_end)
    ]
    accepted_signals = _accepted_source_signals(
        source_signals or [],
        window_start=window_start,
        window_end=effective_window_end,
    )

    platform_presence, platform_presence_reason = _derive_platform_presence(
        current_scores,
        accepted_signals,
        official_support_result,
    )
    cross_platform_match_type, cross_platform_match_reason = _derive_cross_platform_match_type(
        platform_presence,
        accepted_signals,
    )
    official_support_status = _official_support_status(event, official_support_result)
    quality_flags = _quality_flags(current_scores, accepted_signals, official_support_result)
    missing_fields = _missing_fields(current_scores, accepted_signals, official_support_result)

    classification_input = HotspotClassificationInput(
        platform_presence=platform_presence,
        cross_platform_match_type=cross_platform_match_type,
        official_support_status=official_support_status,
        primary_platform_rank=_primary_platform_rank(current_scores),
        snapshot_presence_count=_snapshot_presence_count(current_scores),
        rank_delta_direction=_rank_delta_direction(current_scores),
        search_hit_quality=_search_hit_quality(event_resolution, cross_platform_match_type),
        freshness_bucket="24h",
        source_health=_source_health(accepted_signals),
        quality_flags=quality_flags,
    )
    classification = classify_hotspot(classification_input)
    evidence_summary = _evidence_summary(
        event=event,
        classification=classification,
        platform_presence_reason=platform_presence_reason,
        official_support_result=official_support_result,
        accepted_signals=accepted_signals,
        missing_fields=missing_fields,
    )

    detail_json = _classification_detail_json(
        run_id=run_id,
        window_start=window_start,
        window_end=effective_window_end,
        platform_presence_reason=platform_presence_reason,
        cross_platform_match_reason=cross_platform_match_reason,
        platform_scores=current_scores,
        source_signals=accepted_signals,
        official_support_result=official_support_result,
        evidence_summary=evidence_summary,
        event_resolution=event_resolution,
        missing_fields=missing_fields,
        quality_flags=classification.quality_flags,
    )

    if persist:
        apply_classification_to_event(
            event=event,
            classification=classification,
            evidence_summary=evidence_summary,
            classification_detail_json=detail_json,
        )

    return HotspotClassificationAssembly(
        run_id=run_id,
        event_id=event.event_id or str(event.id),
        window_start=window_start.isoformat(),
        window_end=effective_window_end.isoformat(),
        classification=classification,
        classification_input=classification_input,
        evidence_summary=evidence_summary,
        classification_detail_json=detail_json,
    )


def assemble_hotspot_classification_from_db(
    *,
    run_id: str,
    event: Event,
    db: Any,
    source_signals: list[SourceSignal] | None = None,
    official_support_result: OfficialSupportResult | None = None,
    event_resolution: EventResolution | None = None,
    window_end: datetime | None = None,
    persist: bool = True,
) -> HotspotClassificationAssembly:
    """Read the current rolling 24h PlatformScore rows and assemble classification."""

    effective_window_end = window_end or datetime.now(UTC)
    window_start = effective_window_end - timedelta(hours=WINDOW_HOURS)
    platform_scores = db.scalars(
        select(PlatformScore).where(
            PlatformScore.event_id == event.id,
            PlatformScore.calculated_at >= window_start,
            PlatformScore.calculated_at <= effective_window_end,
        )
    ).all()
    result = assemble_hotspot_classification(
        run_id=run_id,
        event=event,
        platform_scores=list(platform_scores),
        source_signals=source_signals,
        official_support_result=official_support_result,
        event_resolution=event_resolution,
        window_end=effective_window_end,
        persist=persist,
    )
    if persist:
        db.add(event)
        db.commit()
    return result


def apply_classification_to_event(
    *,
    event: Event,
    classification: Any,
    evidence_summary: EvidenceSummary,
    classification_detail_json: dict[str, Any],
) -> Event:
    """Persist key classification fields while replacing only current detail object."""

    detail = dict(event.event_detail_json or {})
    detail["priority_category"] = classification.priority_category
    detail["category_rank"] = classification.category_rank
    detail["platform_presence"] = classification.platform_presence.model_dump(mode="json")
    detail["cross_platform_match_type"] = classification.cross_platform_match_type
    detail["official_support_status"] = classification.official_support_status
    detail["confidence_level"] = classification.confidence_level
    detail["evidence_summary"] = evidence_summary.model_dump(mode="json")
    detail["classification_detail"] = classification_detail_json
    event.event_detail_json = detail
    return event


def _derive_platform_presence(
    platform_scores: list[PlatformScore],
    source_signals: list[SourceSignal],
    official_support_result: OfficialSupportResult | None,
) -> tuple[PlatformPresence, list[str]]:
    presence = PlatformPresence()
    reasons: list[str] = []

    for score in platform_scores:
        score_presence = (score.score_detail_json or {}).get("platform_presence") or {}
        for field_name in ("zhihu_topn", "zhihu_search", "weibo_topn", "weibo_cli"):
            if score_presence.get(field_name):
                setattr(presence, field_name, True)
                reasons.append(f"platform_score:{score.platform}:{field_name}")

    for signal in source_signals:
        if signal.platform == "zhihu" and signal.signal_role == "search_enrichment_signal":
            presence.zhihu_search = True
            reasons.append(f"source_signal:{signal.source_signal_id}:zhihu_search")
        if signal.platform == "weibo" and signal.source_origin == "weibo_cli":
            presence.weibo_cli = True
            reasons.append(f"source_signal:{signal.source_signal_id}:weibo_cli")
        if signal.platform == "zhihu" and signal.signal_role != "search_enrichment_signal":
            presence.zhihu_topn = True
            reasons.append(f"source_signal:{signal.source_signal_id}:zhihu_topn")
        if signal.platform == "weibo" and signal.signal_role != "search_enrichment_signal":
            presence.weibo_topn = True
            reasons.append(f"source_signal:{signal.source_signal_id}:weibo_topn")
        if signal.source_type == "official_news":
            presence.official_source = True
            reasons.append(f"source_signal:{signal.source_signal_id}:official_source")

    if (
        official_support_result is not None
        and official_support_result.official_support_status in {"supported", "weak_supported"}
    ):
        presence.official_source = True
        reasons.append("official_support_result:official_source")

    return presence, _unique(reasons)


def _derive_cross_platform_match_type(
    presence: PlatformPresence,
    source_signals: list[SourceSignal],
) -> tuple[CrossPlatformMatchType, str]:
    if presence.weibo_topn and presence.zhihu_topn:
        return "natural_topn_overlap", "both weibo_topn and zhihu_topn are present"
    if presence.weibo_topn and presence.zhihu_search:
        return _search_supported_type(source_signals, "zhihu"), "weibo_topn with zhihu_search"
    if presence.zhihu_topn and presence.weibo_cli:
        return _search_supported_type(source_signals, "weibo"), "zhihu_topn with weibo_cli"
    if presence.weibo_topn or presence.zhihu_topn:
        return "single_platform_only", "only one community TopN platform is present"
    if presence.official_source:
        return "official_only", "official_source without community TopN presence"
    return "none", "no qualifying platform presence"


def _search_supported_type(
    source_signals: list[SourceSignal],
    search_platform: str,
) -> CrossPlatformMatchType:
    search_signals = [
        signal
        for signal in source_signals
        if signal.platform == search_platform and signal.signal_role == "search_enrichment_signal"
    ]
    if not search_signals:
        return "search_supported"
    if any(
        signal.relation_to_parent is not None and signal.relation_to_parent.confidence < 0.70
        for signal in search_signals
    ):
        return "weak_search_supported"
    return "search_supported"


def _classification_detail_json(
    *,
    run_id: str,
    window_start: datetime,
    window_end: datetime,
    platform_presence_reason: list[str],
    cross_platform_match_reason: str,
    platform_scores: list[PlatformScore],
    source_signals: list[SourceSignal],
    official_support_result: OfficialSupportResult | None,
    evidence_summary: EvidenceSummary,
    event_resolution: EventResolution | None,
    missing_fields: list[str],
    quality_flags: list[str],
) -> dict[str, Any]:
    source_statuses = _unique([str(signal.source_status) for signal in source_signals])
    guardrail_flags = (
        list(event_resolution.match_features_json.guardrail_flags) if event_resolution else []
    )
    if event_resolution and event_resolution.guardrail_status != "pass":
        guardrail_flags.append(event_resolution.guardrail_status)

    return {
        "run_id": run_id,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "platform_presence_reason": platform_presence_reason,
        "cross_platform_match_reason": cross_platform_match_reason,
        "event_id_aggregation_basis": _event_id_aggregation_basis(event_resolution),
        "platform_scores": [_platform_score_summary(score) for score in platform_scores],
        "source_signals": [_source_signal_summary(signal) for signal in source_signals],
        "official_support_detail": (
            official_support_result.official_support_detail.model_dump(mode="json")
            if official_support_result
            else None
        ),
        "evidence_summary": evidence_summary.model_dump(mode="json"),
        "matched_by": list(event_resolution.matched_by) if event_resolution else [],
        "match_features_json": (
            event_resolution.match_features_json.model_dump(mode="json")
            if event_resolution
            else {}
        ),
        "missing_fields": missing_fields,
        "rejected_reasons": _rejected_reasons(event_resolution),
        "source_statuses": source_statuses,
        "quality_flags": _unique(quality_flags),
        "guardrail_flags": _unique(guardrail_flags),
        "updated_at": datetime.now(UTC).isoformat(),
    }


def _evidence_summary(
    *,
    event: Event,
    classification: Any,
    platform_presence_reason: list[str],
    official_support_result: OfficialSupportResult | None,
    accepted_signals: list[SourceSignal],
    missing_fields: list[str],
) -> EvidenceSummary:
    platform_evidence = _platform_evidence(classification.platform_presence)
    official_evidence: list[str] = []
    if official_support_result:
        detail = official_support_result.official_support_detail
        official_evidence.append(
            f"{official_support_result.official_support_status}; "
            f"coverage={detail.official_coverage_level}; "
            f"references={len(official_support_result.official_references)}"
        )
    discussion_evidence = [
        f"{signal.platform}:{signal.signal_role}:{signal.title}"
        for signal in accepted_signals
        if signal.source_type != "official_news"
    ]
    limitations = list(classification.limitations)
    limitations.extend(missing_fields)

    lead = f"{event.title} classified as {classification.priority_category}"
    if platform_presence_reason:
        lead = f"{lead} from current 24h evidence"

    return EvidenceSummary(
        lead=lead,
        platform_evidence=platform_evidence,
        official_evidence=official_evidence,
        discussion_evidence=discussion_evidence,
        limitations=_unique(limitations),
        conservative_language_required=classification.official_support_status == "not_found",
    )


def _platform_evidence(presence: PlatformPresence) -> list[str]:
    evidence = []
    for field_name in ("weibo_topn", "zhihu_topn", "weibo_cli", "zhihu_search"):
        if getattr(presence, field_name):
            evidence.append(field_name)
    return evidence


def _accepted_source_signals(
    source_signals: list[SourceSignal],
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[SourceSignal]:
    accepted = []
    for signal in source_signals:
        signal_time = _datetime_value(signal.published_at) or _datetime_value(signal.fetched_at)
        if signal.audit_only or not signal.contributes_to_classification:
            continue
        if (
            signal.relation_to_parent is not None
            and signal.relation_to_parent.decision != "accepted"
        ):
            continue
        if signal_time and not _in_window(signal_time, window_start, window_end):
            continue
        accepted.append(signal)
    return accepted


def _official_support_status(
    event: Event,
    official_support_result: OfficialSupportResult | None,
) -> OfficialSupportStatus:
    if official_support_result is not None:
        return official_support_result.official_support_status
    detail = event.event_detail_json or {}
    return detail.get("official_support_status") or "not_checked"


def _quality_flags(
    platform_scores: list[PlatformScore],
    source_signals: list[SourceSignal],
    official_support_result: OfficialSupportResult | None,
) -> list[str]:
    flags: list[str] = []
    for score in platform_scores:
        flags.extend((score.score_detail_json or {}).get("quality_flags") or [])
    for signal in source_signals:
        flags.extend(signal.quality_flags)
    if official_support_result:
        flags.extend(official_support_result.quality_flags)
        flags.extend(official_support_result.official_support_detail.quality_flags)
    if not platform_scores and not source_signals and official_support_result is None:
        flags.append("no_classification_evidence")
    return _unique(flags)


def _missing_fields(
    platform_scores: list[PlatformScore],
    source_signals: list[SourceSignal],
    official_support_result: OfficialSupportResult | None,
) -> list[str]:
    missing = []
    if not platform_scores:
        missing.append("platform_scores")
    if not source_signals:
        missing.append("source_signals")
    if official_support_result is None:
        missing.append("official_support_result")
    return missing


def _primary_platform_rank(platform_scores: list[PlatformScore]) -> int | None:
    ranks = []
    for score in platform_scores:
        rank = (score.score_detail_json or {}).get("primary_platform_rank")
        if isinstance(rank, int | float):
            ranks.append(int(rank))
    return min(ranks) if ranks else None


def _snapshot_presence_count(platform_scores: list[PlatformScore]) -> int:
    counts = []
    for score in platform_scores:
        count = (score.score_detail_json or {}).get("snapshot_presence_count")
        if isinstance(count, int):
            counts.append(count)
    return max(counts) if counts else 1


def _rank_delta_direction(platform_scores: list[PlatformScore]) -> str:
    for score in platform_scores:
        for signal_score in (score.score_detail_json or {}).get("signal_scores") or []:
            direction = signal_score.get("rank_delta_direction")
            if direction in {"rising", "stable", "falling"}:
                return direction
    return "unknown"


def _search_hit_quality(
    event_resolution: EventResolution | None,
    cross_platform_match_type: CrossPlatformMatchType,
) -> str:
    if event_resolution is None:
        if cross_platform_match_type.endswith("search_supported"):
            return "keyword_overlap"
        return "none"
    matched_by = set(event_resolution.matched_by)
    features = event_resolution.match_features_json
    if matched_by & {"id_match", "url_match", "title_containment"} or features.title_containment:
        return "same_id_or_title"
    if "entity_time_rule" in matched_by or (
        features.entity_overlap > 0 and features.hard_constraints_passed
    ):
        return "entity_action_match"
    if matched_by & {"keyword_overlap", "bm25_ngram", "embedding_rerank"}:
        return "keyword_overlap"
    return "none"


def _source_health(source_signals: list[SourceSignal]) -> str:
    statuses = {signal.source_status for signal in source_signals}
    if "use" in statuses:
        return "use"
    if statuses & {"fallback", "experimental_fallback", "fallback_pending_credentials"}:
        return "fallback"
    return "unknown"


def _event_id_aggregation_basis(event_resolution: EventResolution | None) -> dict[str, Any]:
    if event_resolution is None:
        return {"basis": "existing_event_id"}
    return {
        "event_id": event_resolution.event_id,
        "event_signal_id": event_resolution.event_signal_id,
        "action": event_resolution.action,
        "confidence": event_resolution.confidence,
        "reason": event_resolution.reason,
    }


def _rejected_reasons(event_resolution: EventResolution | None) -> list[str]:
    if event_resolution is None:
        return []
    reasons = []
    if event_resolution.action in {"reject", "candidate_review"}:
        reasons.append(event_resolution.reason)
    if event_resolution.blocks_auto_analysis:
        reasons.append("blocks_auto_analysis")
    if event_resolution.blocks_publish:
        reasons.append("blocks_publish")
    return _unique(reasons)


def _platform_score_summary(score: PlatformScore) -> dict[str, Any]:
    detail = score.score_detail_json or {}
    return {
        "platform": score.platform,
        "normalized_score": score.normalized_score,
        "calculated_at": _datetime_value(score.calculated_at).isoformat(),
        "platform_bucket": detail.get("platform_bucket"),
        "platform_strength": detail.get("platform_strength"),
        "score_status": detail.get("score_status"),
        "primary_platform_rank": detail.get("primary_platform_rank"),
        "snapshot_presence_count": detail.get("snapshot_presence_count"),
        "platform_presence": detail.get("platform_presence") or {},
        "quality_flags": detail.get("quality_flags") or [],
    }


def _source_signal_summary(signal: SourceSignal) -> dict[str, Any]:
    return {
        "source_signal_id": signal.source_signal_id,
        "item_id": signal.item_id,
        "source_id": signal.source_id,
        "source_type": signal.source_type,
        "source_status": signal.source_status,
        "source_origin": signal.source_origin,
        "platform": signal.platform,
        "signal_role": signal.signal_role,
        "title": signal.title,
        "url": signal.url,
        "relation_decision": (
            signal.relation_to_parent.decision if signal.relation_to_parent else None
        ),
        "quality_flags": signal.quality_flags,
    }


def _datetime_value(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _in_window(value: datetime | None, window_start: datetime, window_end: datetime) -> bool:
    if value is None:
        return False
    return window_start <= value <= window_end


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
