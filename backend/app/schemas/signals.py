"""Event signal schemas and search-enrichment boundaries for Day 23."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.normalized import (
    NormalizedItem,
    Platform,
    SignalRole,
    SourceOrigin,
    SourceStatus,
    SourceType,
)

SearchEnrichmentSource = Literal["zhihu_search", "weibo_cli", "global_search"]
RelationDecision = Literal["accepted", "audit_only", "rejected"]
ConfidenceLevel = Literal["high", "medium", "low", "unknown"]


class SearchEnrichmentRelation(BaseModel):
    """Relevance decision between an enrichment result and an existing candidate."""

    source: SearchEnrichmentSource
    parent_candidate_id: str | None = None
    parent_signal_id: str | None = None
    user_query_id: str | None = None
    parent_title: str
    enrichment_title: str | None = None
    decision: RelationDecision
    confidence: float = Field(ge=0.0, le=1.0)
    matched_by: list[str] = Field(default_factory=list)
    rejected_by: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    audit_only: bool = False

    @model_validator(mode="after")
    def enforce_attachment_boundary(self) -> SearchEnrichmentRelation:
        if self.source != "global_search" and not any(
            [self.parent_candidate_id, self.parent_signal_id, self.user_query_id]
        ):
            raise ValueError("search enrichment must attach to a candidate, signal, or user query")
        if self.decision != "accepted" and not self.audit_only:
            raise ValueError("non-accepted search enrichment results must be audit_only")
        return self


class SourceSignal(BaseModel):
    """Source-level signal after a normalized item is interpreted for events."""

    source_signal_id: str
    item_id: str | None = None
    source_id: str
    source_type: SourceType
    source_status: SourceStatus
    source_origin: SourceOrigin
    platform: Platform
    signal_role: SignalRole
    title: str
    url: str | None = None
    published_at: datetime | str | None = None
    fetched_at: datetime | str
    parent_candidate_id: str | None = None
    parent_signal_id: str | None = None
    user_query_id: str | None = None
    raw_metrics: dict[str, Any] = Field(default_factory=dict)
    platform_features: dict[str, Any] = Field(default_factory=dict)
    classification_features: dict[str, Any] = Field(default_factory=dict)
    relation_to_parent: SearchEnrichmentRelation | None = None
    contributes_to_classification: bool = True
    audit_only: bool = False
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_search_enrichment_boundary(self) -> SourceSignal:
        if self.signal_role == "search_enrichment_signal":
            if not any([self.parent_candidate_id, self.parent_signal_id, self.user_query_id]):
                raise ValueError(
                    "search_enrichment_signal must attach to an existing candidate, "
                    "signal, or user query"
                )
            if self.relation_to_parent is not None and self.relation_to_parent.audit_only:
                self.audit_only = True
                self.contributes_to_classification = False
        if self.audit_only and self.contributes_to_classification:
            raise ValueError("audit_only signals cannot contribute to classification")
        return self


class SemanticFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_text_for_embedding: str | None = None
    embedding_model: str | None = None
    embedding_vector_id: str | None = None
    embedding_created_at: datetime | str | None = None
    embedding_quality_flags: list[str] = Field(default_factory=list)


class EventSignal(BaseModel):
    """Event-level signal extracted from one or more source signals."""

    model_config = ConfigDict(extra="forbid")

    event_signal_id: str
    source_signal_ids: list[str]
    title: str
    keywords: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    event_type: str | None = None
    action_terms: list[str] = Field(default_factory=list)
    event_time_hint: datetime | str | None = None
    event_text_for_match: str
    semantic_fingerprint: SemanticFingerprint = Field(default_factory=SemanticFingerprint)
    confidence: float = Field(ge=0.0, le=1.0)
    is_weak_signal: bool = False
    weak_signal_reason: str | None = None
    source_statuses: list[SourceStatus] = Field(default_factory=list)
    source_roles: list[SignalRole] = Field(default_factory=list)
    platforms: list[Platform] = Field(default_factory=list)
    source_signal_count: int = 1
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_source_and_weak_reason(self) -> EventSignal:
        if not self.source_signal_ids:
            raise ValueError("EventSignal must include at least one source signal")
        if self.is_weak_signal and not self.weak_signal_reason:
            raise ValueError("weak EventSignal must include weak_signal_reason")
        self.source_signal_count = len(self.source_signal_ids)
        return self


def _item_has_source_citation(item: NormalizedItem) -> bool:
    citation = item.source_citation or {}
    citation_url = citation.get("url") or citation.get("link") or citation.get("source_url")
    return bool(item.url or citation_url)


class EventSignalTraceabilityCheck(BaseModel):
    """Validate whether an EventSignal can enter event merge.

    Day 37 keeps citations on NormalizedItem and references them through
    SourceSignal.item_id. This wrapper is the merge-boundary check that prevents
    orphan or audit-only signals from being promoted to EventCandidate/Event.
    """

    event_signal: EventSignal
    source_signals: list[SourceSignal]
    normalized_items: list[NormalizedItem]

    @model_validator(mode="after")
    def ensure_signal_sources_can_be_traced(self) -> EventSignalTraceabilityCheck:
        source_by_id = {signal.source_signal_id: signal for signal in self.source_signals}
        item_by_id = {
            str(item.source_citation.get("item_id") or item.external_id or item.url): item
            for item in self.normalized_items
        }
        missing_source_signal_ids = [
            source_signal_id
            for source_signal_id in self.event_signal.source_signal_ids
            if source_signal_id not in source_by_id
        ]
        if missing_source_signal_ids:
            raise ValueError(
                "EventSignal source_signal_ids must reference provided SourceSignal records"
            )

        for source_signal_id in self.event_signal.source_signal_ids:
            source_signal = source_by_id[source_signal_id]
            if source_signal.audit_only:
                raise ValueError("audit_only SourceSignal cannot enter event merge")
            if not source_signal.item_id:
                raise ValueError("SourceSignal must include item_id for citation traceability")
            item = item_by_id.get(source_signal.item_id)
            if item is None:
                raise ValueError("SourceSignal.item_id must reference a provided NormalizedItem")
            if not _item_has_source_citation(item):
                raise ValueError("EventSignal source traceability requires source citation or URL")
        return self


class ExtractEventSignalsInput(BaseModel):
    """Input contract for the extract_event_signals tool."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    source_signals: list[SourceSignal]
    use_llm: bool = True


class ExtractEventSignalsOutput(BaseModel):
    """Structured output contract for the extract_event_signals tool."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: Literal["not_implemented", "succeeded", "partial", "failed", "skipped"] = (
        "not_implemented"
    )
    event_signals: list[EventSignal] = Field(default_factory=list)
    audit_only_source_signal_ids: list[str] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class EventCandidate(BaseModel):
    """Candidate event assembled before final event resolution."""

    event_candidate_id: str
    title: str
    candidate_key: str
    primary_event_signal_id: str
    event_signal_ids: list[str]
    source_signal_ids: list[str]
    created_from: Literal[
        "official_rss",
        "zhihu_hot_list",
        "weibo_rsshub",
        "user_query",
        "mixed",
    ]
    platform_presence: dict[str, bool] = Field(default_factory=dict)
    source_statuses: list[SourceStatus] = Field(default_factory=list)
    search_enrichment_relations: list[SearchEnrichmentRelation] = Field(default_factory=list)
    rejected_enrichment_count: int = 0
    audit_only_signal_count: int = 0
    confidence_level: ConfidenceLevel = "unknown"
    eligible_for_classification: bool = True
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_candidate_integrity(self) -> EventCandidate:
        if self.primary_event_signal_id not in self.event_signal_ids:
            raise ValueError("primary_event_signal_id must be included in event_signal_ids")
        if not self.source_signal_ids:
            raise ValueError("EventCandidate must include at least one source signal")
        self.rejected_enrichment_count = sum(
            1 for relation in self.search_enrichment_relations if relation.decision == "rejected"
        )
        self.audit_only_signal_count = sum(
            1 for relation in self.search_enrichment_relations if relation.audit_only
        )
        if self.quality_flags and "all_signals_audit_only" in self.quality_flags:
            self.eligible_for_classification = False
        return self
