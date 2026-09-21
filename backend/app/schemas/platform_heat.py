"""Platform-local heat scoring schemas for Day 44."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.normalized import Platform, SignalRole, SourceOrigin

PlatformHeatStatus = Literal["ok", "partial", "unknown"]
PlatformHeatBucket = Literal["top3", "top10", "top20", "tail", "unknown"]


class PlatformHeatConfig(BaseModel):
    """Tunable constants for platform-local heat formulas."""

    zhihu_engagement_cap: int = Field(default=10000, ge=1)
    weibo_search_total_cap: int = Field(default=100000, ge=1)
    weibo_status_count_cap: int = Field(default=50, ge=1)
    weibo_interaction_cap: int = Field(default=10000, ge=1)
    default_topn_window: int = Field(default=20, ge=1)


class PlatformHeatSignalInput(BaseModel):
    """Unified input consumed by platform-local scoring formulas."""

    event_id: str
    item_id: int | str | None = None
    platform: Platform
    source_origin: SourceOrigin
    signal_role: SignalRole
    rank: int | float | None = None
    list_position: int | float | None = None
    vote_count: int | None = None
    comment_count: int | None = None
    topic_present: bool | None = None
    matched_status_count: int | None = None
    weibo_search_total_number_proxy: int | None = None
    top_status_repost_count: int | None = None
    top_status_comment_count: int | None = None
    top_status_like_count: int | None = None
    latest_status_created_at: str | None = None
    published_at: str | None = None
    fetched_at: str | None = None
    snapshot_presence_count: int = Field(default=1, ge=0)
    rank_delta: int | float | None = None
    rank_delta_direction: Literal["rising", "stable", "falling", "unknown"] = "unknown"
    search_enrichment_only: bool = False
    cli_relevance_ok: bool = True
    quality_flags: list[str] = Field(default_factory=list)
    raw_metrics: dict[str, Any] = Field(default_factory=dict)


class PlatformHeatSignalScore(BaseModel):
    """Score and explanation for one platform signal."""

    event_id: str
    platform: Platform
    item_id: int | str | None = None
    signal_score: float | None = Field(default=None, ge=0.0, le=1.0)
    score_status: PlatformHeatStatus
    platform_bucket: PlatformHeatBucket = "unknown"
    primary_platform_rank: int | float | None = None
    rank_delta: int | float | None = None
    snapshot_presence_count: int = 1
    raw_metrics_used: dict[str, Any] = Field(default_factory=dict)
    sub_scores: dict[str, float | None] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)


class PlatformPresenceEvidence(BaseModel):
    zhihu_topn: bool = False
    zhihu_search: bool = False
    weibo_topn: bool = False
    weibo_cli: bool = False


class PlatformHeatScore(BaseModel):
    """Aggregated platform score for one event and one platform."""

    event_id: str
    platform: Platform
    platform_score: float | None = Field(default=None, ge=0.0, le=1.0)
    platform_bucket: PlatformHeatBucket = "unknown"
    platform_strength: Literal["strong", "medium", "weak", "unknown"] = "unknown"
    score_status: PlatformHeatStatus
    primary_platform_rank: int | float | None = None
    rank_delta: int | float | None = None
    snapshot_presence_count: int = 1
    signal_scores: list[PlatformHeatSignalScore] = Field(default_factory=list)
    raw_metrics_used: dict[str, Any] = Field(default_factory=dict)
    sub_scores: dict[str, float | None] = Field(default_factory=dict)
    platform_presence: PlatformPresenceEvidence = Field(default_factory=PlatformPresenceEvidence)
    quality_flags: list[str] = Field(default_factory=list)


class CalculateEventScoresInput(BaseModel):
    """Tool input for Day 44 platform-local scoring."""

    event_ids: list[str] = Field(default_factory=list)
    platforms: list[Literal["zhihu", "weibo"]] = Field(default_factory=lambda: ["zhihu", "weibo"])
    dry_run: bool = False


class CalculateEventScoresOutput(BaseModel):
    """Tool output after platform-local scores are calculated."""

    status: Literal["succeeded", "partial", "failed", "skipped"]
    platform_scores: list[PlatformHeatScore] = Field(default_factory=list)
    saved_count: int = 0
    errors: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
