import pytest
from pydantic import ValidationError

from app.schemas import HotspotClassification, HotspotClassificationInput, PlatformPresence
from app.services.event_classification import classify_hotspot


def classify(
    *,
    presence: PlatformPresence,
    match_type: str = "unknown",
    official: str = "not_found",
    rank: int | None = None,
    search_quality: str = "none",
    snapshots: int = 0,
) -> HotspotClassification:
    return classify_hotspot(
        HotspotClassificationInput(
            platform_presence=presence,
            cross_platform_match_type=match_type,
            official_support_status=official,
            primary_platform_rank=rank,
            search_hit_quality=search_quality,
            snapshot_presence_count=snapshots,
            source_health="use",
        )
    )


def test_classifies_a_cross_platform_with_official() -> None:
    result = classify(
        presence=PlatformPresence(weibo_topn=True, zhihu_topn=True, official_source=True),
        official="supported",
        rank=2,
    )

    assert result.priority_category == "A_cross_platform_with_official"
    assert result.cross_platform_match_type == "natural_topn_overlap"
    assert result.confidence_level == "high"


def test_classifies_b_single_platform_search_with_official() -> None:
    result = classify(
        presence=PlatformPresence(weibo_topn=True, zhihu_search=True, official_source=True),
        match_type="search_supported",
        official="weak_supported",
        rank=7,
        search_quality="same_id_or_title",
    )

    assert result.priority_category == "B_single_platform_with_search_and_official"
    assert result.confidence_level == "high"


def test_classifies_c_cross_platform_without_official() -> None:
    result = classify(
        presence=PlatformPresence(weibo_topn=True, zhihu_search=True),
        match_type="search_supported",
        official="not_found",
        rank=3,
        search_quality="keyword_overlap",
    )

    assert result.priority_category == "C_cross_platform_without_official"
    assert result.confidence_level == "medium"
    assert "official_support_not_found" in result.limitations


def test_classifies_d_single_platform_with_official() -> None:
    result = classify(
        presence=PlatformPresence(zhihu_topn=True, official_source=True),
        official="supported",
        rank=1,
    )

    assert result.priority_category == "D_single_platform_with_official"
    assert result.confidence_level == "medium"


def test_classifies_e_single_platform_only() -> None:
    result = classify(
        presence=PlatformPresence(weibo_topn=True, weibo_cli=True),
        official="not_found",
        rank=12,
        snapshots=1,
    )

    assert result.priority_category == "E_single_platform_only"
    assert result.confidence_level == "low"


def test_classifies_f_official_only() -> None:
    result = classify(
        presence=PlatformPresence(official_source=True),
        official="supported",
    )

    assert result.priority_category == "F_official_only"
    assert result.cross_platform_match_type == "official_only"
    assert result.confidence_level == "medium"


def test_sort_key_prefers_stronger_rank_bucket_within_same_category() -> None:
    top3 = classify(
        presence=PlatformPresence(weibo_topn=True),
        official="not_found",
        rank=2,
    )
    top10 = classify(
        presence=PlatformPresence(weibo_topn=True),
        official="not_found",
        rank=8,
    )

    assert top3.sort_key.as_tuple() < top10.sort_key.as_tuple()


def test_classification_blocks_total_priority_score_flag() -> None:
    result = classify(
        presence=PlatformPresence(weibo_topn=True),
        official="not_found",
    )
    payload = result.model_dump()
    payload["quality_flags"] = ["total_priority_score_used"]

    with pytest.raises(ValidationError, match="total_priority_score"):
        HotspotClassification.model_validate(payload)
