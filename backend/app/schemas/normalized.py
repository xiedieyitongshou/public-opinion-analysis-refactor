"""Shared schemas for Day 22 source normalization contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

SourceStatus = Literal[
    "use",
    "fallback",
    "experimental_fallback",
    "postpone",
    "use_pending_credentials",
    "fallback_pending_credentials",
]

SourceOrigin = Literal[
    "official_rss",
    "official_api",
    "rsshub",
    "weibo_cli",
    "mock",
]

SignalRole = Literal[
    "event_signal",
    "topic_discovery_signal",
    "attention_signal",
    "discussion_focus_signal",
    "evidence_signal",
    "search_enrichment_signal",
    "mixed_signal",
]

SourceType = Literal[
    "official_news",
    "community_hotlist",
    "community_search",
    "community_question_hotlist",
    "community_video_hotlist",
    "mock",
]

Platform = Literal[
    "people",
    "chinanews",
    "xinhua",
    "cctv",
    "weibo",
    "zhihu",
    "bilibili",
    "mock",
]


class NormalizedItem(BaseModel):
    """Canonical item shape after source-specific raw fields are mapped.

    This is structural normalization only. Raw platform metrics remain in
    ``raw_metrics`` and must not be interpreted as cross-platform comparable
    scores.
    """

    source_id: str
    source_name: str
    source_type: SourceType
    source_status: SourceStatus
    source_origin: SourceOrigin
    platform: Platform
    signal_role: SignalRole
    title: str | None
    url: str | None
    fetched_at: datetime | str
    external_id: str | None = None
    channel: str | None = None
    rank: int | float | None = None
    author: str | None = None
    summary: str | None = None
    content: str | None = None
    content_text: str | None = None
    content_hash: str | None = None
    language: str | None = None
    published_at: datetime | str | None = None
    raw_metrics: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    normalized: dict[str, Any] = Field(default_factory=dict)
    source_citation: dict[str, Any] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    signal_contribution_role: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_missing_field_flags(self) -> NormalizedItem:
        flags = set(self.quality_flags)
        if not self.title and "missing_title" not in flags:
            raise ValueError("missing title must be marked with quality_flags=missing_title")
        if not self.url and "missing_url" not in flags:
            raise ValueError("missing url must be marked with quality_flags=missing_url")
        return self


class SourceFieldMapping(BaseModel):
    """Documentation-friendly mapping row for one concrete source."""

    source_id: str
    source_origin: SourceOrigin
    source_status: SourceStatus
    signal_role: SignalRole
    normalized_item_target: dict[str, str]
    raw_metrics_target: dict[str, str]
    required_quality_flags: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
