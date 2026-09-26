"""Pure Day 47 analysis of real, platform-local collection observations."""

from __future__ import annotations

from datetime import timedelta

from app.schemas.platform_trend import (
    PlatformHeatObservation,
    PlatformTrendInput,
    PlatformTrendResult,
)

MAIN_SOURCES = {"zhihu": "zhihu_hot_list", "weibo": "weibo_rsshub_hot_search"}
SCORE_INVALID_FLAGS = {
    "incomparable_scores",
    "freshness_only_change",
    "sampling_changed",
    "score_config_changed",
}
WEIBO_ACTIVITY_METRICS = {
    "weibo_search_total_number_proxy",
    "matched_status_count",
    "top_status_repost_count",
    "top_status_comment_count",
    "top_status_like_count",
}
WEIBO_TREND_METRICS = WEIBO_ACTIVITY_METRICS - {"matched_status_count"}


def analyze_platform_trend(data: PlatformTrendInput) -> PlatformTrendResult:
    """Analyze the current continuous presence segment and latest comparable pair."""

    result = PlatformTrendResult(
        event_id=data.event_id,
        platform=data.platform,
        run_id=data.run_id,
        window_start=data.window_start,
        window_end=data.window_end,
    )
    flags: list[str] = []
    unique: dict[str, PlatformHeatObservation] = {}
    for observation in data.observations:
        if data.window_start < observation.observed_at <= data.window_end:
            unique[observation.observation_id] = observation
        else:
            flags.append("out_of_window_observation")
    observations = sorted(unique.values(), key=lambda item: (item.observed_at, item.observation_id))
    main = [item for item in observations if item.source_id == MAIN_SOURCES[data.platform]]
    positive = [item for item in main if item.topn_present is True]
    result.snapshot_presence_count = len(positive)
    result.first_topn_seen_at = positive[0].observed_at if positive else None
    result.last_topn_seen_at = positive[-1].observed_at if positive else None

    gap = data.config.max_gap_minutes
    if gap is None:
        flags.append("missing_sampling_config")
    if not main:
        flags.extend(["no_window_observations", "no_topn_evidence"])
    else:
        latest = main[-1]
        if gap is None and latest.topn_present is True:
            result.latest_platform_heat = latest.platform_heat
        if latest.collection_status in {"failed", "skipped"}:
            flags.append("source_unavailable")
        if latest.collection_status == "partial" or not latest.list_complete:
            flags.append("incomplete_list")
        if gap is not None and data.window_end - latest.observed_at > timedelta(minutes=gap):
            flags.append("stale_observation")
        elif gap is not None and latest.topn_present is not None:
            result.current_topn_present = latest.topn_present
            if latest.topn_present is False:
                if positive:
                    result.continuous_topn_minutes = 0
                    result.duration_status = "ok"
                else:
                    flags.append("no_topn_evidence")
            else:
                segment = [latest]
                if latest.collection_status == "succeeded" and latest.list_complete:
                    for previous in reversed(main[:-1]):
                        if (
                            previous.topn_present is not True
                            or previous.collection_status != "succeeded"
                            or not previous.list_complete
                        ):
                            break
                        if segment[-1].observed_at - previous.observed_at > timedelta(minutes=gap):
                            flags.append("observation_gap")
                            break
                        if not latest.topn_scope or previous.topn_scope != latest.topn_scope:
                            flags.append("incomparable_scope")
                            break
                        segment.append(previous)
                result.continuous_topn_minutes = min(
                    1440.0,
                    (latest.observed_at - segment[-1].observed_at).total_seconds() / 60,
                )
                result.duration_status = "ok" if len(segment) > 1 else "partial"
                if len(segment) == 1:
                    flags.append("insufficient_history")
                result.latest_platform_heat = latest.platform_heat
                _set_trend(result, main, gap, data.config.rank_delta_threshold,
                           data.config.score_delta_threshold, flags)
        else:
            flags.append("current_presence_unknown")

    # Search/CLI-only evidence may explain a static partial score but never creates duration.
    auxiliary = [item for item in observations if item.source_id != MAIN_SOURCES[data.platform]]
    if not positive and auxiliary and gap is not None:
        latest_auxiliary = auxiliary[-1]
        if (
            latest_auxiliary.platform_heat is not None
            and data.window_end - latest_auxiliary.observed_at <= timedelta(minutes=gap)
        ):
            result.latest_platform_heat = latest_auxiliary.platform_heat
    result.quality_flags = list(dict.fromkeys(flags))
    result.limitations = _limitations(result.quality_flags)
    return result


def _set_trend(
    result: PlatformTrendResult,
    main: list[PlatformHeatObservation],
    gap: float,
    rank_threshold: int,
    score_threshold: float,
    flags: list[str],
) -> None:
    if len(main) < 2:
        flags.append("insufficient_history")
        return
    previous, current = main[-2:]
    if previous.topn_present is not True or current.topn_present is not True:
        flags.append("insufficient_trend_metrics")
        return
    if any(
        item.collection_status != "succeeded" or not item.list_complete
        for item in (previous, current)
    ):
        flags.append("incomplete_list")
        return
    if current.observed_at - previous.observed_at > timedelta(minutes=gap):
        flags.append("observation_gap")
        return
    if not current.topn_scope or previous.topn_scope != current.topn_scope:
        flags.append("incomparable_scope")
        return
    if result.platform == "zhihu" and previous.rank is not None and current.rank is not None:
        delta = previous.rank - current.rank
        result.rank_delta = delta
        result.trend_basis = "rank"
        result.trend_status = _direction(delta, rank_threshold)
        result.comparison_observation_ids = [previous.observation_id, current.observation_id]
        return
    if result.platform == "weibo" and not _weibo_has_activity(previous, current):
        flags.append("insufficient_trend_metrics")
        return
    if not _comparable_scores(previous, current):
        flags.append("incomparable_scores")
        return
    delta = current.platform_heat.platform_score - previous.platform_heat.platform_score
    if result.platform == "weibo" and not _weibo_activity_changed(previous, current):
        flags.append("incomparable_scores")
        return
    result.score_delta = round(delta, 6)
    result.trend_basis = "platform_score"
    result.trend_status = _direction(delta, score_threshold)
    result.comparison_observation_ids = [previous.observation_id, current.observation_id]


def _comparable_scores(previous: PlatformHeatObservation, current: PlatformHeatObservation) -> bool:
    if not all((
        previous.platform_heat,
        current.platform_heat,
        previous.score_config_version,
        current.score_config_version,
        previous.sampling_signature,
        current.sampling_signature,
        previous.score_component_signature,
        current.score_component_signature,
    )):
        return False
    if (
        previous.score_config_version != current.score_config_version
        or previous.sampling_signature != current.sampling_signature
        or previous.score_component_signature != current.score_component_signature
        or (set(previous.quality_flags) | set(current.quality_flags)) & SCORE_INVALID_FLAGS
    ):
        return False
    return (
        previous.platform_heat.platform_score is not None
        and current.platform_heat.platform_score is not None
        and previous.platform_heat.score_status != "unknown"
        and current.platform_heat.score_status != "unknown"
    )


def _weibo_has_activity(
    previous: PlatformHeatObservation, current: PlatformHeatObservation
) -> bool:
    return all(
        item.platform_heat is not None
        and any(item.platform_heat.raw_metrics_used.get(key) is not None
                for key in WEIBO_ACTIVITY_METRICS)
        for item in (previous, current)
    )


def _weibo_activity_changed(
    previous: PlatformHeatObservation, current: PlatformHeatObservation
) -> bool:
    return any(
        previous.platform_heat.raw_metrics_used.get(key)
        != current.platform_heat.raw_metrics_used.get(key)
        for key in WEIBO_TREND_METRICS
    )


def _direction(delta: float, threshold: float) -> str:
    if delta >= threshold:
        return "rising"
    if delta <= -threshold:
        return "cooling"
    return "stable"


def _limitations(flags: list[str]) -> list[str]:
    explanations = {
        "insufficient_history": "Only one current top-list observation; duration is a lower bound.",
        "source_unavailable": "The latest collection did not provide a usable list.",
        "incomplete_list": "List completeness was not established.",
        "stale_observation": "The latest list is older than the configured sampling gap.",
        "missing_sampling_config": "Sampling interval is not configured.",
        "incomparable_scores": "The latest platform scores are not comparable.",
    }
    return [explanations[flag] for flag in flags if flag in explanations]
