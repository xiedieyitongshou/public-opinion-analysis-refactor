"""Official media support schemas for Day 43."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.display import OfficialSupportStatus
from app.schemas.normalized import SourceStatus

OfficialFreshnessBucket = Literal["24h", "72h", "7d", "stale", "unknown"]
OfficialMatchQuality = Literal["strong", "weak", "none", "unknown"]
OfficialCoverageLevel = Literal[
    "none",
    "single_source",
    "multi_source_duplicate",
    "multi_source_independent",
    "unknown",
]


class OfficialSupportConfig(BaseModel):
    """Rule thresholds for official media evidence matching."""

    recall_limit: int = Field(default=20, ge=1, le=100)
    time_window_days: int = Field(default=7, ge=1)
    keyword_overlap_supported: float = Field(default=0.50, ge=0.0, le=1.0)
    keyword_overlap_weak: float = Field(default=0.25, ge=0.0, le=1.0)
    ngram_overlap_supported: float = Field(default=0.45, ge=0.0, le=1.0)
    ngram_overlap_weak: float = Field(default=0.20, ge=0.0, le=1.0)


class OfficialReference(BaseModel):
    """Traceable official media reference used to explain support status."""

    item_id: str | None = None
    title: str
    url: str | None = None
    source_name: str
    source_status: SourceStatus | str
    published_at: str | None = None
    match_reason: str
    match_features: dict[str, Any] = Field(default_factory=dict)
    raw_metric: dict[str, Any] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)


class OfficialSupportDetail(BaseModel):
    """Auditable summary of how the support status was assigned."""

    official_source_count: int = 0
    official_item_count: int = 0
    source_coverage_count: int = 0
    unique_story_count: int = 0
    official_coverage_level: OfficialCoverageLevel = "unknown"
    authority_sources: list[str] = Field(default_factory=list)
    freshness_bucket: OfficialFreshnessBucket = "unknown"
    match_quality: OfficialMatchQuality = "unknown"
    matched_item_ids: list[str] = Field(default_factory=list)
    source_independence_flags: list[str] = Field(default_factory=list)
    official_query: dict[str, Any] | None = None
    quality_flags: list[str] = Field(default_factory=list)


class OfficialSupportResult(BaseModel):
    """Official media support result consumed by hotspot classification."""

    event_id: str | None = None
    official_support_status: OfficialSupportStatus
    official_references: list[OfficialReference] = Field(default_factory=list)
    official_support_detail: OfficialSupportDetail = Field(default_factory=OfficialSupportDetail)
    quality_flags: list[str] = Field(default_factory=list)
