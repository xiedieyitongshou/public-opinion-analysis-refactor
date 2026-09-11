"""Classification schemas for Day 25 hotspot categories and sort boundaries."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.display import (
    CrossPlatformMatchType,
    OfficialSupportStatus,
    PlatformPresence,
    PriorityCategory,
)
from app.schemas.signals import ConfidenceLevel

SearchHitQuality = Literal[
    "same_id_or_title",
    "entity_action_match",
    "keyword_overlap",
    "none",
    "unknown",
]

RankDeltaDirection = Literal["rising", "stable", "falling", "unknown"]
FreshnessBucket = Literal["24h", "72h", "7d", "stale", "unknown"]
SourceHealth = Literal["use", "fallback", "unavailable", "unknown"]
PlatformBucket = Literal["top3", "top10", "top20", "tail", "unknown"]


class HotspotClassificationInput(BaseModel):
    """Minimal evidence needed to classify an event candidate for MVP output."""

    platform_presence: PlatformPresence = Field(default_factory=PlatformPresence)
    cross_platform_match_type: CrossPlatformMatchType = "unknown"
    official_support_status: OfficialSupportStatus = "not_checked"
    primary_platform_rank: int | None = None
    snapshot_presence_count: int = 0
    rank_delta_direction: RankDeltaDirection = "unknown"
    search_hit_quality: SearchHitQuality = "unknown"
    freshness_bucket: FreshnessBucket = "unknown"
    source_health: SourceHealth = "unknown"
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_cross_platform_presence(self) -> HotspotClassificationInput:
        has_topn_overlap = self.platform_presence.weibo_topn and self.platform_presence.zhihu_topn
        if has_topn_overlap and self.cross_platform_match_type == "unknown":
            self.cross_platform_match_type = "natural_topn_overlap"
        return self


class HotspotSortKey(BaseModel):
    """Lexicographic sort key. Lower values sort earlier."""

    category_rank: int
    official_support_rank: int
    match_strength_rank: int
    primary_rank_bucket_rank: int
    snapshot_presence_rank: int
    rank_delta_rank: int
    search_hit_quality_rank: int
    freshness_rank: int
    source_health_rank: int
    noise_rank: int

    def as_tuple(self) -> tuple[int, ...]:
        return (
            self.category_rank,
            self.official_support_rank,
            self.match_strength_rank,
            self.primary_rank_bucket_rank,
            self.snapshot_presence_rank,
            self.rank_delta_rank,
            self.search_hit_quality_rank,
            self.freshness_rank,
            self.source_health_rank,
            self.noise_rank,
        )


class HotspotClassification(BaseModel):
    """Classification result stored in EventCard.classification_detail."""

    priority_category: PriorityCategory
    confidence_level: ConfidenceLevel
    category_rank: int
    cross_platform_match_type: CrossPlatformMatchType
    official_support_status: OfficialSupportStatus
    platform_presence: PlatformPresence
    sort_key: HotspotSortKey
    reasons: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def block_cross_platform_score_fields(self) -> HotspotClassification:
        if any(flag.startswith("total_priority_score") for flag in self.quality_flags):
            raise ValueError("MVP classification must not use total_priority_score")
        return self
