"""Human review queue schemas for Day 41."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.matching import EventMergeCandidateReview

HumanReviewStatus = Literal["pending", "resolved", "ignored", "cancelled"]
HumanReviewType = Literal["event_merge_candidate", "briefing_quality", "publish_decision"]
HumanReviewDecision = Literal["merge_to_existing", "create_new_event", "ignore", "reject"]


class HumanReviewTaskRecord(BaseModel):
    id: int
    review_type: HumanReviewType | str
    status: HumanReviewStatus | str
    priority: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    reviewer: str | None = None
    decision: HumanReviewDecision | str | None = None
    decision_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    resolved_at: str | None = None


class CreateHumanReviewTaskInput(BaseModel):
    """Tool input for creating review tasks from Day 40 candidate reviews."""

    review_type: HumanReviewType = "event_merge_candidate"
    run_id: str | None = None
    candidate_review: EventMergeCandidateReview | None = None
    candidate_reviews: list[EventMergeCandidateReview] = Field(default_factory=list)
    source_citations: list[dict[str, Any]] = Field(default_factory=list)
    priority: int = Field(default=0, ge=0, le=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class CreateHumanReviewTaskOutput(BaseModel):
    status: Literal["succeeded", "skipped", "failed"]
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    human_review_tasks: list[HumanReviewTaskRecord] = Field(default_factory=list)
    message: str | None = None


class HumanReviewDecisionInput(BaseModel):
    decision: HumanReviewDecision
    reviewer: str | None = None
    decision_reason: str | None = None
    event_title: str | None = None


class HumanReviewDecisionOutput(BaseModel):
    status: Literal["succeeded", "failed"]
    human_review_task: HumanReviewTaskRecord
    event_id: str | None = None
    message: str | None = None
