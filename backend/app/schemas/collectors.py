"""Collector runtime schemas for Day 29."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.normalized import (
    NormalizedItem,
    Platform,
    SignalRole,
    SourceOrigin,
    SourceStatus,
    SourceType,
)

CollectorRunStatus = Literal["succeeded", "partial", "failed", "skipped"]
SourceRunStatus = Literal["succeeded", "failed", "skipped"]


class CollectorMetadata(BaseModel):
    """Static contract every concrete collector must declare."""

    source_id: str
    source_name: str
    source_type: SourceType
    source_status: SourceStatus
    source_origin: SourceOrigin
    platform: Platform
    signal_role: SignalRole
    signal_contribution_role: list[str] = Field(default_factory=list)
    default_limit: int = Field(default=20, ge=1, le=100)
    max_limit: int = Field(default=50, ge=1, le=500)

    @model_validator(mode="after")
    def ensure_default_limit_within_max(self) -> CollectorMetadata:
        if self.default_limit > self.max_limit:
            raise ValueError("default_limit must be less than or equal to max_limit")
        return self


class CollectorRunConfig(BaseModel):
    """Per-source execution config passed from tool calls to collectors."""

    source_id: str
    limit: int = Field(default=20, ge=1, le=500)
    validate_only: bool = True
    dry_run: bool = True
    promote_to_event_pool: bool = False
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_quota_cost: int | None = Field(default=None, ge=0)
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validation_mode_must_not_write(self) -> CollectorRunConfig:
        if self.validate_only:
            self.dry_run = True
            self.promote_to_event_pool = False
        return self


class CrawlValidationResult(BaseModel):
    """Observable result for one collector execution."""

    source_id: str
    source_name: str | None = None
    source_status: SourceStatus | Literal["unknown"] = "unknown"
    source_type: SourceType | Literal["unknown"] = "unknown"
    source_origin: SourceOrigin | Literal["unknown"] = "unknown"
    platform: Platform | Literal["unknown"] = "unknown"
    signal_role: SignalRole | Literal["unknown"] = "unknown"
    status: SourceRunStatus
    validate_only: bool = True
    dry_run: bool = True
    requested_limit: int = 0
    returned_count: int = 0
    normalized_count: int = 0
    saved_count: int = 0
    field_completeness: dict[str, float] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    error_message: str | None = None
    quota_before: dict[str, Any] | None = None
    quota_after: dict[str, Any] | None = None
    raw_sample_path: str | None = None
    duration_ms: int | None = None
    normalized_items: list[dict[str, Any]] = Field(default_factory=list)


class FetchSourceItemsInput(BaseModel):
    """Input schema for the `fetch_source_items` tool."""

    source_ids: list[str] | None = None
    limit: int = Field(default=20, ge=1, le=100)
    per_source_limits: dict[str, int] = Field(default_factory=dict)
    validate_only: bool = True
    dry_run: bool = True
    promote_to_event_pool: bool = False
    include_items: bool = True
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_quota_cost: int | None = Field(default=None, ge=0)
    per_source_params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    postponed_source_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validation_mode_must_not_write(self) -> FetchSourceItemsInput:
        if self.validate_only:
            self.dry_run = True
            self.promote_to_event_pool = False
        return self


class FetchSourceItemsOutput(BaseModel):
    """Output schema for the `fetch_source_items` tool."""

    status: CollectorRunStatus
    validate_only: bool
    dry_run: bool
    promote_to_event_pool: bool = False
    requested_source_ids: list[str]
    results: list[CrawlValidationResult] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)
    unavailable_source_ids: list[str] = Field(default_factory=list)
    skipped_source_ids: list[str] = Field(default_factory=list)
    source_status_summary: dict[str, int] = Field(default_factory=dict)
    contribution_roles_by_source: dict[str, list[str]] = Field(default_factory=dict)
    field_completeness_summary: dict[str, float] = Field(default_factory=dict)
    validation_summary: dict[str, Any] = Field(default_factory=dict)
    blocks_auto_analysis: bool = False


class NormalizeRawItemsInput(BaseModel):
    """Input schema for the `normalize_raw_items` tool."""

    items: list[dict[str, Any]] = Field(default_factory=list)
    source_defaults: dict[str, Any] = Field(default_factory=dict)


class NormalizeRawItemsOutput(BaseModel):
    """Output schema for deterministic Day 36 normalization."""

    status: Literal["succeeded", "partial", "failed", "skipped"]
    normalized_count: int = 0
    items: list[NormalizedItem] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    error_count: int = 0
    errors: list[str] = Field(default_factory=list)
