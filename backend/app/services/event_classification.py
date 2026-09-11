"""Rule-based MVP hotspot classification for Day 25."""

from __future__ import annotations

from app.schemas import (
    HotspotClassification,
    HotspotClassificationInput,
    HotspotSortKey,
    PlatformPresence,
)
from app.schemas.display import CrossPlatformMatchType, PriorityCategory
from app.schemas.signals import ConfidenceLevel

OFFICIAL_SUPPORT_RANK = {
    "supported": 0,
    "weak_supported": 1,
    "not_found": 2,
    "not_checked": 3,
}

MATCH_STRENGTH_RANK = {
    "natural_topn_overlap": 0,
    "search_supported": 1,
    "weak_search_supported": 2,
    "single_platform_only": 3,
    "official_only": 4,
    "none": 5,
    "unknown": 6,
}

RANK_BUCKET_RANK = {"top3": 0, "top10": 1, "top20": 2, "tail": 3, "unknown": 4}
RANK_DELTA_RANK = {"rising": 0, "stable": 1, "falling": 2, "unknown": 3}
SEARCH_HIT_RANK = {
    "same_id_or_title": 0,
    "entity_action_match": 1,
    "keyword_overlap": 2,
    "none": 3,
    "unknown": 4,
}
FRESHNESS_RANK = {"24h": 0, "72h": 1, "7d": 2, "stale": 3, "unknown": 4}
SOURCE_HEALTH_RANK = {"use": 0, "fallback": 1, "unavailable": 2, "unknown": 3}

CATEGORY_RANK = {
    "A_cross_platform_with_official": 0,
    "B_single_platform_with_search_and_official": 1,
    "C_cross_platform_without_official": 2,
    "D_single_platform_with_official": 3,
    "E_single_platform_only": 4,
    "F_official_only": 5,
    "unknown": 6,
}


def classify_hotspot(input_data: HotspotClassificationInput) -> HotspotClassification:
    """Classify one event candidate without producing a cross-platform score."""

    category, reasons, limitations = _priority_category(input_data)
    confidence = _confidence_level(input_data, category)
    sort_key = _sort_key(input_data, category)

    return HotspotClassification(
        priority_category=category,
        confidence_level=confidence,
        category_rank=CATEGORY_RANK[category],
        cross_platform_match_type=_normalize_match_type(input_data),
        official_support_status=input_data.official_support_status,
        platform_presence=input_data.platform_presence,
        sort_key=sort_key,
        reasons=reasons,
        limitations=limitations,
        quality_flags=input_data.quality_flags,
    )


def _priority_category(
    input_data: HotspotClassificationInput,
) -> tuple[PriorityCategory, list[str], list[str]]:
    presence = input_data.platform_presence
    official_supported = input_data.official_support_status in {"supported", "weak_supported"}
    official_found = input_data.official_support_status == "supported"
    official_weak = input_data.official_support_status == "weak_supported"
    has_community_topn = presence.zhihu_topn or presence.weibo_topn
    has_natural_topn_overlap = presence.zhihu_topn and presence.weibo_topn
    has_search_support = input_data.cross_platform_match_type in {
        "search_supported",
        "weak_search_supported",
    }
    has_cross_platform = has_natural_topn_overlap or has_search_support
    reasons: list[str] = []
    limitations: list[str] = []

    if has_natural_topn_overlap:
        reasons.append("both_community_platforms_topn")
    if has_search_support:
        reasons.append(input_data.cross_platform_match_type)
    if official_found:
        reasons.append("official_supported")
    elif official_weak:
        reasons.append("official_weak_supported")
    elif input_data.official_support_status == "not_found":
        limitations.append("official_support_not_found")

    if has_natural_topn_overlap and official_supported:
        return "A_cross_platform_with_official", reasons, limitations

    if has_search_support and official_supported and has_community_topn:
        return "B_single_platform_with_search_and_official", reasons, limitations

    if has_cross_platform and not official_supported:
        return "C_cross_platform_without_official", reasons, limitations

    if has_community_topn and official_supported:
        return "D_single_platform_with_official", reasons, limitations

    if has_community_topn:
        limitations.append("single_platform_or_no_cross_platform_support")
        return "E_single_platform_only", reasons, limitations

    if presence.official_source:
        reasons.append("official_source_only")
        return "F_official_only", reasons, limitations

    limitations.append("insufficient_presence")
    return "unknown", reasons, limitations


def _confidence_level(
    input_data: HotspotClassificationInput,
    category: PriorityCategory,
) -> ConfidenceLevel:
    if "source_unavailable" in input_data.quality_flags:
        return "low"
    if category == "A_cross_platform_with_official":
        return "high"
    if category == "B_single_platform_with_search_and_official":
        return "high" if input_data.search_hit_quality == "same_id_or_title" else "medium"
    if category in {"C_cross_platform_without_official", "D_single_platform_with_official"}:
        return "medium"
    if category == "E_single_platform_only":
        if input_data.snapshot_presence_count >= 2 or input_data.search_hit_quality != "none":
            return "medium"
        return "low"
    if category == "F_official_only":
        return "medium" if input_data.official_support_status == "supported" else "low"
    return "unknown"


def _sort_key(
    input_data: HotspotClassificationInput,
    category: PriorityCategory,
) -> HotspotSortKey:
    return HotspotSortKey(
        category_rank=CATEGORY_RANK[category],
        official_support_rank=OFFICIAL_SUPPORT_RANK[input_data.official_support_status],
        match_strength_rank=MATCH_STRENGTH_RANK[_normalize_match_type(input_data)],
        primary_rank_bucket_rank=RANK_BUCKET_RANK[_rank_bucket(input_data.primary_platform_rank)],
        snapshot_presence_rank=-max(0, input_data.snapshot_presence_count),
        rank_delta_rank=RANK_DELTA_RANK[input_data.rank_delta_direction],
        search_hit_quality_rank=SEARCH_HIT_RANK[input_data.search_hit_quality],
        freshness_rank=FRESHNESS_RANK[input_data.freshness_bucket],
        source_health_rank=SOURCE_HEALTH_RANK[input_data.source_health],
        noise_rank=len(input_data.quality_flags),
    )


def _normalize_match_type(input_data: HotspotClassificationInput) -> CrossPlatformMatchType:
    if (
        input_data.cross_platform_match_type == "unknown"
        and input_data.platform_presence.zhihu_topn
        and input_data.platform_presence.weibo_topn
    ):
        return "natural_topn_overlap"
    if input_data.cross_platform_match_type == "unknown":
        if _has_single_platform_topn(input_data.platform_presence):
            return "single_platform_only"
        if input_data.platform_presence.official_source:
            return "official_only"
        return "none"
    return input_data.cross_platform_match_type


def _has_single_platform_topn(presence: PlatformPresence) -> bool:
    return (presence.zhihu_topn or presence.weibo_topn) and not (
        presence.zhihu_topn and presence.weibo_topn
    )


def _rank_bucket(rank: int | None) -> str:
    if rank is None:
        return "unknown"
    if rank <= 3:
        return "top3"
    if rank <= 10:
        return "top10"
    if rank <= 20:
        return "top20"
    return "tail"
