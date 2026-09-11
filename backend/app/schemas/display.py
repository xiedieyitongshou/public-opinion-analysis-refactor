"""Display-layer structured output schemas for Day 24."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.normalized import Platform, SourceOrigin, SourceStatus, SourceType
from app.schemas.signals import ConfidenceLevel

PriorityCategory = Literal[
    "A_cross_platform_with_official",
    "B_single_platform_with_search_and_official",
    "C_cross_platform_without_official",
    "D_single_platform_with_official",
    "E_single_platform_only",
    "F_official_only",
    "unknown",
]

CrossPlatformMatchType = Literal[
    "natural_topn_overlap",
    "search_supported",
    "weak_search_supported",
    "single_platform_only",
    "official_only",
    "none",
    "unknown",
]

OfficialSupportStatus = Literal["supported", "weak_supported", "not_found", "not_checked"]
PublishEligibility = Literal["ready_for_review", "needs_review", "blocked_for_publish"]
BriefingSectionType = Literal[
    "overview",
    "top_events",
    "domain_hotspots",
    "platform_hotspots",
    "rising_events",
    "risk_notes",
    "sources",
]
DraftStatus = Literal["draft", "draft_ready_for_review", "draft_needs_review", "draft_blocked"]
PublishStatus = Literal["published", "published_with_warning", "blocked", "draft"]


class SourceCitation(BaseModel):
    """Citation details required to trace an event card back to source data."""

    item_id: str | None = None
    source_signal_id: str | None = None
    title: str
    url: str | None = None
    source_name: str
    source_type: SourceType
    source_status: SourceStatus
    source_origin: SourceOrigin
    platform: Platform
    published_at: datetime | str | None = None
    fetched_at: datetime | str
    quality_flags: list[str] = Field(default_factory=list)
    raw_metrics_used: dict[str, Any] = Field(default_factory=dict)
    citation_role: Literal[
        "primary",
        "supporting",
        "discussion",
        "official_support",
        "search_enrichment",
        "audit",
    ] = "supporting"

    @model_validator(mode="after")
    def require_missing_url_flag(self) -> SourceCitation:
        if not self.url and "missing_url" not in self.quality_flags:
            raise ValueError("missing citation url must be marked with quality_flags=missing_url")
        return self


class PlatformPresence(BaseModel):
    """Where the event is visible in the current evidence set."""

    zhihu_topn: bool = False
    zhihu_search: bool = False
    weibo_topn: bool = False
    weibo_cli: bool = False
    official_source: bool = False
    mock: bool = False


class EvidenceSummary(BaseModel):
    """Human-facing summary of why the event is included."""

    lead: str
    platform_evidence: list[str] = Field(default_factory=list)
    official_evidence: list[str] = Field(default_factory=list)
    discussion_evidence: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    conservative_language_required: bool = False


class EventCard(BaseModel):
    """Display card for one event in a briefing or event search result."""

    event_id: str
    title: str
    summary: str
    domain: str | None = None
    priority_category: PriorityCategory = "unknown"
    category_rank: int | None = None
    platform_presence: PlatformPresence = Field(default_factory=PlatformPresence)
    cross_platform_match_type: CrossPlatformMatchType = "unknown"
    official_support_status: OfficialSupportStatus = "not_checked"
    confidence_level: ConfidenceLevel = "unknown"
    primary_platform: Platform | None = None
    primary_platform_rank: int | None = None
    platform_bucket: str | None = None
    platform_strength: str | None = None
    rank_delta: int | float | None = None
    trend_status: str = "unknown"
    evidence_summary: EvidenceSummary
    source_citations: list[SourceCitation]
    quality_flags: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    publish_eligibility: PublishEligibility = "needs_review"
    classification_detail: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_display_guardrails(self) -> EventCard:
        if not self.source_citations:
            raise ValueError("EventCard must include at least one SourceCitation")
        if self.official_support_status == "not_found":
            self.evidence_summary.conservative_language_required = True
            if "official_support_not_found" not in self.risk_notes:
                self.risk_notes.append("official_support_not_found")
        if self.confidence_level == "low" and self.publish_eligibility == "ready_for_review":
            raise ValueError("low-confidence EventCard cannot be ready_for_review")
        return self


class BriefingSection(BaseModel):
    section_type: BriefingSectionType
    title: str
    summary: str | None = None
    event_cards: list[EventCard] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DailyBriefing(BaseModel):
    daily_report_id: str | None = None
    run_id: str
    report_date: date | str
    title: str
    summary: str
    sections: list[BriefingSection]
    draft_status: DraftStatus = "draft"
    publish_status: PublishStatus | None = None
    source_citation_count: int = 0
    risk_notes: list[str] = Field(default_factory=list)
    created_at: datetime | str
    retention_until: datetime | str | None = None

    @model_validator(mode="after")
    def count_citations_and_require_sections(self) -> DailyBriefing:
        if not self.sections:
            raise ValueError("DailyBriefing must include at least one section")
        self.source_citation_count = sum(
            len(card.source_citations)
            for section in self.sections
            for card in section.event_cards
        )
        return self


class EventQuery(BaseModel):
    event_query_id: str
    query: str
    platforms: list[Platform] = Field(default_factory=list)
    time_window: dict[str, datetime | str | None] | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | str


class RetrievedEvidence(BaseModel):
    evidence_id: str
    event_id: str | None = None
    item_id: str | None = None
    source_signal_id: str | None = None
    title: str
    url: str | None = None
    source_name: str
    platform: Platform
    published_at: datetime | str | None = None
    match_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    quality_flags: list[str] = Field(default_factory=list)


class EventSearchResult(BaseModel):
    event_query_id: str
    matched_events: list[EventCard] = Field(default_factory=list)
    retrieved_evidence: list[RetrievedEvidence] = Field(default_factory=list)
    platform_heat: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    generated_at: datetime | str
