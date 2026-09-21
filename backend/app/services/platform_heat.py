"""Platform-local heat scoring service for Day 44."""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from app.schemas import (
    PlatformHeatConfig,
    PlatformHeatScore,
    PlatformHeatSignalInput,
    PlatformHeatSignalScore,
    PlatformPresenceEvidence,
)


def item_to_platform_heat_signal(item: Any, *, event_id: str) -> PlatformHeatSignalInput:
    """Map an Item-like object into the formula input shape."""

    raw_metrics = _dict_value(item, "raw_metrics_json", "raw_metrics", default={}) or {}
    normalized = _dict_value(item, "normalized_json", "normalized", default={}) or {}
    quality_flags = _list_value(item, "quality_flags_json", "quality_flags")
    source = getattr(item, "source", None)
    source_origin = _string_value(item, "source_origin") or _string_value(
        normalized, "source_origin"
    )
    signal_role = _string_value(item, "signal_role") or _string_value(normalized, "signal_role")
    relation = normalized.get("relation") if isinstance(normalized, dict) else None
    if not isinstance(relation, dict):
        relation = {}

    return PlatformHeatSignalInput(
        event_id=event_id,
        item_id=_dict_value(item, "id", "item_id"),
        platform=(
            _string_value(item, "platform")
            or _string_value(normalized, "platform")
            or _string_value(source, "platform")
        ),
        source_origin=source_origin,
        signal_role=signal_role,
        rank=_number_value(item, "rank") or _number_value(raw_metrics, "rank", "platform_rank"),
        list_position=_number_value(raw_metrics, "list_position"),
        vote_count=_int_value(raw_metrics, "vote_count", "vote_up_count"),
        comment_count=_int_value(raw_metrics, "comment_count", "comments_count"),
        topic_present=_bool_value(normalized, "topic_present"),
        matched_status_count=_int_value(raw_metrics, "matched_status_count"),
        weibo_search_total_number_proxy=_int_value(
            raw_metrics,
            "weibo_search_total_number_proxy",
            "total_number_proxy",
        ),
        top_status_repost_count=_int_value(raw_metrics, "top_status_repost_count", "reposts_count"),
        top_status_comment_count=_int_value(
            raw_metrics,
            "top_status_comment_count",
            "comments_count",
        ),
        top_status_like_count=_int_value(raw_metrics, "top_status_like_count", "attitudes_count"),
        latest_status_created_at=_string_value(
            raw_metrics,
            "latest_status_created_at",
            "latest_status_created_at_after_relevance_filter",
        ),
        published_at=_datetime_string(item, "published_at"),
        fetched_at=_datetime_string(item, "fetched_at"),
        snapshot_presence_count=_int_value(normalized, "snapshot_presence_count") or 1,
        rank_delta=_number_value(normalized, "rank_delta"),
        rank_delta_direction=_rank_delta_direction(normalized),
        search_enrichment_only=signal_role == "search_enrichment_signal",
        cli_relevance_ok=not bool(relation.get("audit_only"))
        and "low_relevance" not in quality_flags
        and "audit_only" not in quality_flags,
        quality_flags=quality_flags,
        raw_metrics=raw_metrics if isinstance(raw_metrics, dict) else {},
    )


def score_zhihu_platform(
    signal: PlatformHeatSignalInput,
    config: PlatformHeatConfig | None = None,
) -> PlatformHeatSignalScore:
    """Score one Zhihu signal without treating search-only as TopN."""

    config = config or PlatformHeatConfig()
    flags = list(signal.quality_flags)
    if signal.search_enrichment_only:
        flags.append("search_enrichment_only")

    rank_component = None if signal.search_enrichment_only else rank_score(signal.rank)
    snapshot_component = _snapshot_presence_score(signal.snapshot_presence_count)
    rank_delta_component = _rank_delta_score(signal.rank_delta_direction)
    engagement_component = _engagement_score(
        signal.vote_count,
        signal.comment_count,
        cap=config.zhihu_engagement_cap,
    )
    if signal.snapshot_presence_count <= 1:
        flags.append("insufficient_history")
        rank_delta_component = None

    score = weighted_available(
        [
            (rank_component, 0.50),
            (snapshot_component, 0.20),
            (rank_delta_component, 0.15),
            (engagement_component, 0.15),
        ]
    )
    status = _status(
        score,
        ok=rank_component is not None
        and (engagement_component is not None or signal.snapshot_presence_count > 1),
    )
    return PlatformHeatSignalScore(
        event_id=signal.event_id,
        platform="zhihu",
        item_id=signal.item_id,
        signal_score=score,
        score_status=status,
        platform_bucket=_bucket(score, signal.rank),
        primary_platform_rank=signal.rank if not signal.search_enrichment_only else None,
        rank_delta=signal.rank_delta,
        snapshot_presence_count=max(1, signal.snapshot_presence_count),
        raw_metrics_used=_used_metrics(
            {
                "rank": signal.rank if not signal.search_enrichment_only else None,
                "vote_count": signal.vote_count,
                "comment_count": signal.comment_count,
            }
        ),
        sub_scores={
            "rank_score": rank_component,
            "snapshot_presence_score": snapshot_component,
            "rank_delta_score": rank_delta_component,
            "engagement_score": engagement_component,
        },
        quality_flags=_dedupe(flags),
    )


def score_weibo_platform(
    signal: PlatformHeatSignalInput,
    config: PlatformHeatConfig | None = None,
) -> PlatformHeatSignalScore:
    """Score one Weibo signal with RSSHub as TopN seed and CLI as enrichment."""

    config = config or PlatformHeatConfig()
    flags = list(signal.quality_flags)
    is_rsshub_seed = (
        signal.source_origin == "rsshub" and signal.signal_role == "topic_discovery_signal"
    )
    is_cli_signal = signal.source_origin == "weibo_cli"
    if is_cli_signal:
        flags.append("weibo_cli_only")
    if is_cli_signal and not signal.cli_relevance_ok:
        flags.append("weibo_cli_low_relevance")
        return _unknown_signal(signal, "weibo", flags)

    topic_component = (
        _topic_presence_score(signal, config)
        if is_rsshub_seed
        else None
    )
    content_component = _weibo_content_activity_score(signal, config)
    interaction_component = _weibo_interaction_score(signal, config)
    score = weighted_available(
        [
            (topic_component, 0.40),
            (content_component, 0.35),
            (interaction_component, 0.25),
        ]
    )
    has_cli_enrichment = content_component is not None or interaction_component is not None
    status = _status(score, ok=is_rsshub_seed and has_cli_enrichment)
    if signal.snapshot_presence_count <= 1:
        flags.append("insufficient_history")

    rank = signal.list_position if is_rsshub_seed else None
    return PlatformHeatSignalScore(
        event_id=signal.event_id,
        platform="weibo",
        item_id=signal.item_id,
        signal_score=score,
        score_status=status,
        platform_bucket=_bucket(score, rank),
        primary_platform_rank=rank,
        rank_delta=signal.rank_delta,
        snapshot_presence_count=max(1, signal.snapshot_presence_count),
        raw_metrics_used=_used_metrics(
            {
                "list_position": signal.list_position if is_rsshub_seed else None,
                "topic_present": signal.topic_present if is_rsshub_seed else None,
                "weibo_search_total_number_proxy": signal.weibo_search_total_number_proxy,
                "matched_status_count": signal.matched_status_count,
                "top_status_repost_count": signal.top_status_repost_count,
                "top_status_comment_count": signal.top_status_comment_count,
                "top_status_like_count": signal.top_status_like_count,
            }
        ),
        sub_scores={
            "topic_presence_score": topic_component,
            "content_activity_score": content_component,
            "interaction_sample_score": interaction_component,
        },
        quality_flags=_dedupe(flags),
    )


def aggregate_platform_scores(
    scores: Iterable[PlatformHeatSignalScore],
) -> list[PlatformHeatScore]:
    """Aggregate signal scores by event_id + platform using max + capped bonus."""

    grouped: dict[tuple[str, str], list[PlatformHeatSignalScore]] = {}
    for score in scores:
        grouped.setdefault((score.event_id, score.platform), []).append(score)

    results: list[PlatformHeatScore] = []
    for (event_id, platform), signal_scores in grouped.items():
        usable_scores = [score for score in signal_scores if score.signal_score is not None]
        flags = _dedupe(flag for score in signal_scores for flag in score.quality_flags)
        if not usable_scores:
            results.append(
                PlatformHeatScore(
                    event_id=event_id,
                    platform=platform,
                    score_status="unknown",
                    signal_scores=signal_scores,
                    quality_flags=flags,
                    platform_presence=_presence(platform, signal_scores),
                )
            )
            continue

        best = max(usable_scores, key=lambda score: score.signal_score or 0.0)
        extra_signal_count = max(0, len(usable_scores) - 1)
        platform_score = min(1.0, (best.signal_score or 0.0) + 0.05 * min(3, extra_signal_count))
        status = "ok" if any(score.score_status == "ok" for score in usable_scores) else "partial"
        raw_metrics_used: dict[str, Any] = {}
        for score in usable_scores:
            raw_metrics_used.update(score.raw_metrics_used)
        results.append(
            PlatformHeatScore(
                event_id=event_id,
                platform=platform,
                platform_score=platform_score,
                platform_bucket=_bucket(platform_score, best.primary_platform_rank),
                platform_strength=_strength(platform_score),
                score_status=status,
                primary_platform_rank=best.primary_platform_rank,
                rank_delta=best.rank_delta,
                snapshot_presence_count=max(
                    score.snapshot_presence_count for score in usable_scores
                ),
                signal_scores=signal_scores,
                raw_metrics_used=raw_metrics_used,
                sub_scores=best.sub_scores,
                platform_presence=_presence(platform, signal_scores),
                quality_flags=flags,
            )
        )
    return results


def score_platform_signals(
    signals: Iterable[PlatformHeatSignalInput],
    config: PlatformHeatConfig | None = None,
) -> list[PlatformHeatScore]:
    """Score all supported signals and aggregate by platform."""

    config = config or PlatformHeatConfig()
    signal_scores: list[PlatformHeatSignalScore] = []
    for signal in signals:
        if signal.platform == "zhihu":
            signal_scores.append(score_zhihu_platform(signal, config))
        elif signal.platform == "weibo":
            signal_scores.append(score_weibo_platform(signal, config))
    return aggregate_platform_scores(signal_scores)


def weighted_available(items: Iterable[tuple[float | None, float]]) -> float | None:
    """Average available sub-scores with weights re-normalized over available values."""

    total_weight = 0.0
    weighted_sum = 0.0
    for score, weight in items:
        if score is None:
            continue
        total_weight += weight
        weighted_sum += score * weight
    if total_weight <= 0:
        return None
    return max(0.0, min(1.0, weighted_sum / total_weight))


def normalize_log(value: int | float | None, cap: int | float) -> float | None:
    if value is None or value < 0:
        return None
    return min(1.0, math.log1p(value) / math.log1p(cap))


def rank_score(rank: int | float | None, *, window: int = 20) -> float | None:
    if rank is None or rank <= 0:
        return None
    return max(0.0, min(1.0, 1 - (rank - 1) / window))


def freshness_score(value: str | datetime | None, *, now: datetime | None = None) -> float | None:
    timestamp = _as_datetime(value)
    if timestamp is None:
        return None
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    hours = abs((now.astimezone(UTC) - timestamp.astimezone(UTC)).total_seconds()) / 3600
    if hours <= 6:
        return 1.0
    if hours <= 24:
        return 0.8
    if hours <= 72:
        return 0.5
    if hours <= 168:
        return 0.25
    return 0.10


def _topic_presence_score(
    signal: PlatformHeatSignalInput,
    config: PlatformHeatConfig,
) -> float | None:
    if not signal.topic_present:
        return None
    rank = signal.list_position or signal.rank
    base = 0.50
    snapshot_bonus = min(0.30, 0.10 * max(1, signal.snapshot_presence_count))
    rank_bonus = 0.0
    if rank is not None:
        if rank <= 3:
            rank_bonus = 0.20
        elif rank <= 10:
            rank_bonus = 0.15
        elif rank <= config.default_topn_window:
            rank_bonus = 0.10
        else:
            rank_bonus = 0.05
    return min(1.0, base + snapshot_bonus + rank_bonus)


def _weibo_content_activity_score(
    signal: PlatformHeatSignalInput,
    config: PlatformHeatConfig,
) -> float | None:
    return weighted_available(
        [
            (
                normalize_log(
                    signal.weibo_search_total_number_proxy,
                    config.weibo_search_total_cap,
                ),
                0.50,
            ),
            (normalize_log(signal.matched_status_count, config.weibo_status_count_cap), 0.30),
            (freshness_score(signal.latest_status_created_at or signal.published_at), 0.20),
        ]
    )


def _weibo_interaction_score(
    signal: PlatformHeatSignalInput,
    config: PlatformHeatConfig,
) -> float | None:
    values = [
        signal.top_status_repost_count,
        signal.top_status_comment_count,
        signal.top_status_like_count,
    ]
    if all(value is None for value in values):
        return None
    return normalize_log(sum(value or 0 for value in values), config.weibo_interaction_cap)


def _engagement_score(
    vote_count: int | None,
    comment_count: int | None,
    *,
    cap: int,
) -> float | None:
    if vote_count is None and comment_count is None:
        return None
    return normalize_log((vote_count or 0) + (comment_count or 0), cap)


def _snapshot_presence_score(count: int) -> float | None:
    if count <= 0:
        return None
    return min(1.0, count / 3)


def _rank_delta_score(direction: str) -> float | None:
    return {
        "rising": 1.0,
        "stable": 0.60,
        "falling": 0.30,
        "unknown": None,
    }.get(direction, None)


def _status(score: float | None, *, ok: bool) -> str:
    if score is None:
        return "unknown"
    return "ok" if ok else "partial"


def _unknown_signal(
    signal: PlatformHeatSignalInput,
    platform: str,
    flags: list[str],
) -> PlatformHeatSignalScore:
    return PlatformHeatSignalScore(
        event_id=signal.event_id,
        platform=platform,
        item_id=signal.item_id,
        signal_score=None,
        score_status="unknown",
        quality_flags=_dedupe(flags),
    )


def _bucket(score: float | None, rank: int | float | None = None) -> str:
    if rank is not None:
        if rank <= 3:
            return "top3"
        if rank <= 10:
            return "top10"
        if rank <= 20:
            return "top20"
        return "tail"
    if score is None:
        return "unknown"
    if score >= 0.85:
        return "top3"
    if score >= 0.70:
        return "top10"
    if score >= 0.50:
        return "top20"
    return "tail"


def _strength(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 0.70:
        return "strong"
    if score >= 0.45:
        return "medium"
    return "weak"


def _presence(platform: str, scores: list[PlatformHeatSignalScore]) -> PlatformPresenceEvidence:
    presence = PlatformPresenceEvidence()
    if platform == "zhihu":
        presence.zhihu_topn = any(
            score.primary_platform_rank is not None and score.score_status != "unknown"
            for score in scores
        )
        presence.zhihu_search = any(
            "search_enrichment_only" in score.quality_flags for score in scores
        )
    if platform == "weibo":
        presence.weibo_topn = any(
            score.primary_platform_rank is not None and score.score_status != "unknown"
            for score in scores
        )
        presence.weibo_cli = any(
            "weibo_cli_only" in score.quality_flags
            or "weibo_search_total_number_proxy" in score.raw_metrics_used
            for score in scores
        )
    return presence


def _used_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if value is not None}


def _dict_value(item: Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if isinstance(item, dict) and key in item:
            return item[key]
        if not isinstance(item, dict) and hasattr(item, key):
            return getattr(item, key)
    return default


def _string_value(item: Any, *keys: str) -> str | None:
    value = _dict_value(item, *keys)
    if value is None:
        return None
    return str(value)


def _list_value(item: Any, *keys: str) -> list[str]:
    value = _dict_value(item, *keys, default=[])
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _number_value(item: Any, *keys: str) -> int | float | None:
    value = _dict_value(item, *keys)
    if value is None:
        return None
    if isinstance(value, int | float):
        return value
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return int(parsed) if parsed.is_integer() else parsed


def _int_value(item: Any, *keys: str) -> int | None:
    value = _number_value(item, *keys)
    if value is None:
        return None
    return int(value)


def _bool_value(item: Any, *keys: str) -> bool | None:
    value = _dict_value(item, *keys)
    if value is None:
        return None
    return bool(value)


def _datetime_string(item: Any, *keys: str) -> str | None:
    value = _dict_value(item, *keys)
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _rank_delta_direction(normalized: dict[str, Any]) -> str:
    direction = str(normalized.get("rank_delta_direction") or "unknown")
    if direction in {"rising", "stable", "falling", "unknown"}:
        return direction
    delta = _number_value(normalized, "rank_delta")
    if delta is None:
        return "unknown"
    if delta < 0:
        return "rising"
    if delta > 0:
        return "falling"
    return "stable"


def _as_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
