"""Explicit profiles shared by the real workflow and ablation runner."""

from typing import Literal

from app.schemas.matching import EventMatchConfig
from app.schemas.official_support import OfficialSupportConfig

MatchingProfile = Literal["rules", "hybrid", "hybrid_rerank"]


def profile_flags(profile: MatchingProfile) -> dict:
    if profile not in {"rules", "hybrid", "hybrid_rerank"}:
        raise ValueError("unknown matching profile")
    return {"use_embedding": profile != "rules", "use_reranker": profile == "hybrid_rerank"}


def event_config(profile: MatchingProfile) -> EventMatchConfig:
    return EventMatchConfig(**profile_flags(profile))


def official_config(profile: MatchingProfile) -> OfficialSupportConfig:
    return OfficialSupportConfig(**profile_flags(profile))
