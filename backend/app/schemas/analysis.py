"""Contracts connecting collection, event construction and platform analysis."""

from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from app.schemas.classification import HotspotClassificationAssembly
from app.schemas.collectors import FetchSourceItemsInput, FetchSourceItemsOutput
from app.schemas.matching import EventResolution, SourceSignalMatchRef
from app.schemas.normalized import NormalizedItem
from app.schemas.platform_trend import EventHeatAnalysis, PlatformTrendConfig
from app.schemas.signals import SourceSignal


class HotspotAnalysisInput(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    collection: FetchSourceItemsInput = Field(
        default_factory=lambda: FetchSourceItemsInput(
            validate_only=False,
            dry_run=False,
            promote_to_event_pool=False,
        )
    )
    window_end: datetime | None = None
    trend_configs: dict[Literal["zhihu", "weibo"], PlatformTrendConfig] = Field(
        default_factory=dict
    )
    use_llm: bool = False
    matching_profile: Literal["rules", "hybrid", "hybrid_rerank"] | None = None
    official_search_enabled: bool | None = None

    @model_validator(mode="after")
    def require_analysis_collection(self):
        if self.collection.validate_only or self.collection.dry_run:
            raise ValueError("Analysis requires a real collection or a replay in an isolated DB")
        if self.window_end is not None and self.window_end.tzinfo is None:
            raise ValueError("window_end must include a timezone")
        return self


class PrepareSourceSignalsInput(BaseModel):
    run_id: str
    collection: FetchSourceItemsOutput
    items: list[NormalizedItem]
    window_end: datetime | None = None


class PrepareSourceSignalsOutput(BaseModel):
    status: Literal["succeeded", "partial", "failed", "skipped"]
    window_end: datetime
    items: list[NormalizedItem] = Field(default_factory=list)
    source_signals: list[SourceSignal] = Field(default_factory=list)
    source_refs_by_signal_id: dict[str, SourceSignalMatchRef] = Field(default_factory=dict)
    saved_count: int = 0
    excluded_count: int = 0
    quality_flags: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ClassifyEventsInput(BaseModel):
    run_id: str
    window_end: datetime
    event_ids: list[str]
    collection: FetchSourceItemsOutput
    event_resolutions: list[EventResolution] = Field(default_factory=list)
    matching_profile: Literal["rules", "hybrid", "hybrid_rerank"] = "rules"
    official_search_enabled: bool = False
    # Zero permits reuse of cached evidence without making new external requests.
    official_search_budget: int | None = Field(default=None, ge=0)


class ClassifyEventsOutput(BaseModel):
    status: Literal["succeeded", "partial", "skipped"]
    classifications: list[HotspotClassificationAssembly] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)


class HotspotAnalysisOutput(BaseModel):
    run_id: str
    plan_id: str
    status: Literal["succeeded", "partial", "failed", "skipped"]
    window_end: datetime | None = None
    event_ids: list[str] = Field(default_factory=list)
    source_statuses: dict[str, str] = Field(default_factory=dict)
    tasks: list[dict] = Field(default_factory=list)
    classifications: list[HotspotClassificationAssembly] = Field(default_factory=list)
    analyses: list[EventHeatAnalysis] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
