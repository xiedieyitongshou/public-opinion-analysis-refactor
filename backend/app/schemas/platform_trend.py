"""Contracts for Day 47 platform-local, observation-based heat trends."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.collectors import FetchSourceItemsOutput
from app.schemas.platform_heat import PlatformHeatScore

TrendPlatform = Literal["zhihu", "weibo"]
PlatformTrendStatus = Literal["rising", "stable", "cooling", "unknown"]
DurationStatus = Literal["ok", "partial", "unknown"]
CollectionStatus = Literal["succeeded", "partial", "failed", "skipped"]


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observation and window times must include a timezone")
    return value.astimezone(UTC)


class PlatformHeatObservation(BaseModel):
    event_id: str
    platform: TrendPlatform
    observation_id: str
    run_id: str
    observed_at: datetime
    source_id: str
    collection_status: CollectionStatus
    list_complete: bool = False
    topn_scope: str | None = None
    topn_present: bool | None = None
    rank: int | None = Field(default=None, ge=1)
    platform_score_id: int | None = None
    platform_heat: PlatformHeatScore | None = None
    score_config_version: str | None = None
    sampling_signature: str | None = None
    score_component_signature: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_observation(self) -> PlatformHeatObservation:
        self.observed_at = utc(self.observed_at)
        if self.platform_heat is not None and (
            self.platform_heat.event_id != self.event_id
            or self.platform_heat.platform != self.platform
        ):
            raise ValueError("platform heat must belong to the same event and platform")
        if self.rank is not None and (
            self.platform != "zhihu" or self.source_id != "zhihu_hot_list"
        ):
            raise ValueError("rank is only valid for the Zhihu official hot list")
        if self.topn_present is False and (
            not self.list_complete
            or self.collection_status != "succeeded"
            or not self.topn_scope
        ):
            raise ValueError("absence requires a successful, complete list")
        if self.topn_present is True and self.collection_status in {"failed", "skipped"}:
            raise ValueError("failed or skipped collection cannot establish presence")
        if self.rank is not None and self.topn_present is not True:
            raise ValueError("rank requires observed hot-list presence")
        return self


class PlatformTrendConfig(BaseModel):
    version: str = "day47-v1"
    window_hours: Literal[24] = 24
    expected_interval_minutes: float | None = Field(default=None, gt=0)
    max_gap_minutes: float | None = Field(default=None, gt=0)
    rank_delta_threshold: int = Field(default=1, ge=1)
    score_delta_threshold: float = Field(default=0.05, gt=0, le=1)

    @model_validator(mode="after")
    def default_gap(self) -> PlatformTrendConfig:
        if self.max_gap_minutes is None and self.expected_interval_minutes is not None:
            self.max_gap_minutes = 2 * self.expected_interval_minutes
        return self


class PlatformTrendInput(BaseModel):
    event_id: str
    platform: TrendPlatform
    run_id: str
    window_start: datetime
    window_end: datetime
    observations: list[PlatformHeatObservation] = Field(default_factory=list)
    config: PlatformTrendConfig = Field(default_factory=PlatformTrendConfig)

    @model_validator(mode="after")
    def validate_window_and_identity(self) -> PlatformTrendInput:
        self.window_start = utc(self.window_start)
        self.window_end = utc(self.window_end)
        if self.window_end - self.window_start != timedelta(hours=self.config.window_hours):
            raise ValueError("trend window must match configured window_hours")
        if any(
            item.event_id != self.event_id or item.platform != self.platform
            for item in self.observations
        ):
            raise ValueError("observations must belong to the input event and platform")
        return self


class PlatformTrendResult(BaseModel):
    event_id: str
    platform: TrendPlatform
    run_id: str
    window_start: datetime
    window_end: datetime
    first_topn_seen_at: datetime | None = None
    last_topn_seen_at: datetime | None = None
    snapshot_presence_count: int = Field(default=0, ge=0)
    current_topn_present: bool | None = None
    continuous_topn_minutes: float | None = Field(default=None, ge=0, le=1440)
    duration_status: DurationStatus = "unknown"
    latest_platform_heat: PlatformHeatScore | None = None
    trend_status: PlatformTrendStatus = "unknown"
    trend_basis: Literal["rank", "platform_score", "none"] = "none"
    comparison_observation_ids: list[str] = Field(default_factory=list)
    rank_delta: int | None = None
    score_delta: float | None = None
    quality_flags: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class EventHeatAnalysis(BaseModel):
    event_id: str
    run_id: str
    window_start: datetime
    window_end: datetime
    platforms: dict[TrendPlatform, PlatformTrendResult]
    status: Literal["ok", "partial", "unknown"]
    quality_flags: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def require_both_platforms(self) -> EventHeatAnalysis:
        if set(self.platforms) != {"zhihu", "weibo"}:
            raise ValueError("analysis requires separate zhihu and weibo results")
        if any(
            result.event_id != self.event_id
            or result.run_id != self.run_id
            or result.window_start != self.window_start
            or result.window_end != self.window_end
            or result.platform != platform
            for platform, result in self.platforms.items()
        ):
            raise ValueError("platform results must match the analysis identity and window")
        return self


class AnalyzeEventHeatInput(BaseModel):
    run_id: str
    event_ids: list[str] = Field(default_factory=list)
    window_end: datetime | None = None
    configs: dict[TrendPlatform, PlatformTrendConfig] = Field(default_factory=dict)
    collection: FetchSourceItemsOutput | None = None
    event_ids_by_content_hash: dict[str, str] = Field(default_factory=dict)
    dry_run: bool = False


class AnalyzeEventHeatOutput(BaseModel):
    status: Literal["succeeded", "partial", "failed", "skipped"]
    analyses: list[EventHeatAnalysis] = Field(default_factory=list)
    saved_observation_count: int = 0
    errors: list[str] = Field(default_factory=list)
