"""Event matching schemas for Day 39."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.signals import EventSignal

EventResolutionAction = Literal["merge", "create", "candidate_review", "reject", "skip"]
MatchMethod = Literal[
    "id_match",
    "url_match",
    "title_containment",
    "entity_time_rule",
    "keyword_overlap",
    "bm25_ngram",
    "embedding_rerank",
]


class SourceSignalMatchRef(BaseModel):
    """Minimal source-level hard identifiers used only by the matcher."""

    source_signal_id: str
    url: str | None = None
    platform_id: str | None = None


class EventMatchConfig(BaseModel):
    """Tunable Day 39 matching thresholds."""

    use_bm25_ngram: bool = True
    use_embedding: bool = True
    keyword_overlap_high: float = Field(default=0.55, ge=0.0, le=1.0)
    ngram_overlap_high: float = Field(default=0.50, ge=0.0, le=1.0)
    bm25_candidate_min_score: float = Field(default=0.45, ge=0.0, le=1.0)
    embedding_candidate_min_similarity: float = Field(default=0.78, ge=0.0, le=1.0)
    embedding_auto_merge_min_similarity: float = Field(default=0.86, ge=0.0, le=1.0)
    time_window_same_event_hours: int = Field(default=72, ge=1)
    auto_merge_confidence_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    candidate_review_confidence_threshold: float = Field(default=0.60, ge=0.0, le=1.0)


class MatchFeatures(BaseModel):
    """Auditable feature bundle saved with each match decision."""

    id_match: bool = False
    url_match: bool = False
    title_containment: bool = False
    keyword_overlap: float = Field(default=0.0, ge=0.0, le=1.0)
    ngram_overlap: float = Field(default=0.0, ge=0.0, le=1.0)
    bm25_score: float = Field(default=0.0, ge=0.0, le=1.0)
    embedding_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    entity_overlap: float = Field(default=0.0, ge=0.0, le=1.0)
    action_overlap: float = Field(default=0.0, ge=0.0, le=1.0)
    object_overlap: float | None = None
    time_distance_hours: float | None = Field(default=None, ge=0.0)
    hard_constraints_passed: bool = False
    guardrail_flags: list[str] = Field(default_factory=list)
    matched_by: list[MatchMethod] = Field(default_factory=list)


class RetrievedEventMatchCandidate(BaseModel):
    """Existing event shape used by the matcher without requiring DB models."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    title: str
    event_fingerprint: str | None = None
    keywords: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    action_terms: list[str] = Field(default_factory=list)
    event_type: str | None = None
    event_time_hint: datetime | str | None = None
    first_seen_at: datetime | str | None = None
    last_seen_at: datetime | str | None = None
    event_text_for_match: str | None = None
    event_text_for_embedding: str | None = None
    event_signal_ids: list[str] = Field(default_factory=list)
    source_signal_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    platform_ids: list[str] = Field(default_factory=list)


class RerankEventMatchResult(BaseModel):
    candidate: RetrievedEventMatchCandidate
    confidence: float = Field(ge=0.0, le=1.0)
    features: MatchFeatures
    reason: str


class EventMergeCandidateReview(BaseModel):
    """Stable payload that Day 41 can expose in the human review queue."""

    review_type: Literal["event_merge_candidate"] = "event_merge_candidate"
    event_signal_id: str
    candidate_event_id: str | None = None
    recommended_action: EventResolutionAction
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    matched_by: list[MatchMethod] = Field(default_factory=list)
    match_features_json: MatchFeatures = Field(default_factory=MatchFeatures)
    guardrail_flags: list[str] = Field(default_factory=list)
    source_signal_ids: list[str] = Field(default_factory=list)


class EventResolution(BaseModel):
    event_signal_id: str
    event_id: str
    event_fingerprint: str
    action: EventResolutionAction
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    matched_by: list[MatchMethod] = Field(default_factory=list)
    match_features_json: MatchFeatures = Field(default_factory=MatchFeatures)
    matched_candidate_event_id: str | None = None
    review_required: bool = False
    blocks_auto_analysis: bool = False
    blocks_publish: bool = False
    guardrail_status: Literal["pass", "warn", "block"] = "pass"
    candidate_review: EventMergeCandidateReview | None = None


class MatchAndResolveEventsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    event_signals: list[EventSignal] = Field(default_factory=list)
    existing_events: list[RetrievedEventMatchCandidate] = Field(default_factory=list)
    source_refs_by_signal_id: dict[str, SourceSignalMatchRef] = Field(default_factory=dict)
    match_config: EventMatchConfig = Field(default_factory=EventMatchConfig)


class MatchAndResolveEventsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: Literal["succeeded", "partial", "failed", "skipped"]
    event_resolutions: list[EventResolution] = Field(default_factory=list)
    created_event_count: int = 0
    merged_event_count: int = 0
    review_required_count: int = 0
    rejected_event_count: int = 0
    quality_flags: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class EventMatchRetrievalInput(BaseModel):
    run_id: str
    event_signal: EventSignal
    candidate_events: list[RetrievedEventMatchCandidate] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=100)


class EventMatchRetrievalOutput(BaseModel):
    run_id: str
    retrieved_candidates: list[RetrievedEventMatchCandidate] = Field(default_factory=list)


class RerankEventMatchesInput(BaseModel):
    run_id: str
    event_signal: EventSignal
    retrieved_candidates: list[RetrievedEventMatchCandidate] = Field(default_factory=list)
    match_config: EventMatchConfig = Field(default_factory=EventMatchConfig)


class RerankEventMatchesOutput(BaseModel):
    run_id: str
    results: list[RerankEventMatchResult] = Field(default_factory=list)
