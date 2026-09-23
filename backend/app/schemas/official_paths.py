"""Official media path orchestration schemas for Day 45."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.matching import EventResolution
from app.schemas.official_support import OfficialSupportResult
from app.schemas.signals import SourceSignal

OfficialPathType = Literal["community_support", "official_agenda"]
OfficialPathStatus = Literal["succeeded", "partial", "failed", "skipped"]


class OfficialQuery(BaseModel):
    """Query generated for community-event official support enrichment."""

    query_text: str
    event_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    generated_from: Literal["event_title", "event_entity_keywords", "manual"] = "event_title"
    quality_flags: list[str] = Field(default_factory=list)


class OfficialAgendaRank(BaseModel):
    """Internal ranking key for F_official_only / high-evidence low-discussion lanes."""

    official_source_count: int = 0
    source_coverage_count: int = 0
    unique_story_count: int = 0
    freshness_bucket: str = "unknown"
    authority_sources: list[str] = Field(default_factory=list)
    source_status_rank: int = 2

    def as_tuple(self) -> tuple[int, int, int, int, int]:
        freshness_rank = {"24h": 0, "72h": 1, "7d": 2, "stale": 3, "unknown": 4}.get(
            self.freshness_bucket,
            4,
        )
        return (
            -self.unique_story_count,
            -self.source_coverage_count,
            -self.official_source_count,
            freshness_rank,
            self.source_status_rank,
        )


class OfficialPathResult(BaseModel):
    """Unified output for Day 45 official media paths A and B."""

    path_type: OfficialPathType
    status: OfficialPathStatus
    official_query: OfficialQuery | None = None
    official_support_result: OfficialSupportResult | None = None
    source_signals: list[SourceSignal] = Field(default_factory=list)
    event_resolutions: list[EventResolution] = Field(default_factory=list)
    official_agenda_rank: OfficialAgendaRank | None = None
    eligible_for_community_main_list: bool = False
    fallback_used: bool = False
    output_references: list[dict[str, Any]] = Field(default_factory=list)
    downstream_fields: dict[str, Any] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
