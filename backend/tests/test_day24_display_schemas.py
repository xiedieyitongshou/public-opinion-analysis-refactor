import pytest
from pydantic import ValidationError

from app.schemas import (
    BriefingSection,
    DailyBriefing,
    EventCard,
    EventQuery,
    EventSearchResult,
    PlatformPresence,
    RetrievedEvidence,
    SourceCitation,
)


def citation(**overrides: object) -> SourceCitation:
    payload: dict[str, object] = {
        "item_id": "item-1",
        "title": "事件来源",
        "url": "https://example.com/news/1",
        "source_name": "知乎热榜",
        "source_type": "community_hotlist",
        "source_status": "use",
        "source_origin": "official_api",
        "platform": "zhihu",
        "fetched_at": "2026-09-11T10:00:00+08:00",
        "quality_flags": [],
    }
    payload.update(overrides)
    return SourceCitation.model_validate(payload)


def event_card(**overrides: object) -> EventCard:
    payload: dict[str, object] = {
        "event_id": "event-1",
        "title": "社区热点事件",
        "summary": "知乎和微博出现相关讨论。",
        "priority_category": "E_single_platform_only",
        "category_rank": 1,
        "platform_presence": {"zhihu_topn": True},
        "cross_platform_match_type": "single_platform_only",
        "official_support_status": "not_found",
        "confidence_level": "medium",
        "primary_platform": "zhihu",
        "primary_platform_rank": 3,
        "evidence_summary": {
            "lead": "知乎热榜出现该候选。",
            "platform_evidence": ["知乎 hot_list rank 3"],
            "limitations": ["官媒样本未命中"],
        },
        "source_citations": [citation()],
        "publish_eligibility": "needs_review",
    }
    payload.update(overrides)
    return EventCard.model_validate(payload)


def test_source_citation_requires_missing_url_flag() -> None:
    with pytest.raises(ValidationError, match="missing_url"):
        citation(url=None)

    assert citation(url=None, quality_flags=["missing_url"]).url is None


def test_event_card_requires_source_citation() -> None:
    with pytest.raises(ValidationError, match="SourceCitation"):
        event_card(source_citations=[])


def test_event_card_marks_no_official_support_as_conservative() -> None:
    card = event_card()

    assert card.evidence_summary.conservative_language_required is True
    assert "official_support_not_found" in card.risk_notes


def test_low_confidence_card_cannot_be_ready_for_review() -> None:
    with pytest.raises(ValidationError, match="low-confidence"):
        event_card(confidence_level="low", publish_eligibility="ready_for_review")


def test_daily_briefing_counts_source_citations() -> None:
    briefing = DailyBriefing(
        run_id="run-1",
        report_date="2026-09-11",
        title="每日热点简报",
        summary="今日热点摘要。",
        sections=[
            BriefingSection(
                section_type="top_events",
                title="热点事件",
                event_cards=[event_card()],
            )
        ],
        created_at="2026-09-11T10:00:00+08:00",
    )

    assert briefing.source_citation_count == 1


def test_event_search_result_supports_mode_b_evidence() -> None:
    query = EventQuery(
        event_query_id="query-1",
        query="太子奶创始人",
        platforms=["weibo", "zhihu"],
        created_at="2026-09-11T10:00:00+08:00",
    )
    result = EventSearchResult(
        event_query_id=query.event_query_id,
        matched_events=[event_card(platform_presence=PlatformPresence(zhihu_topn=True))],
        retrieved_evidence=[
            RetrievedEvidence(
                evidence_id="evidence-1",
                title="相关证据",
                url="https://example.com/evidence",
                source_name="微博 CLI 搜索",
                platform="weibo",
                match_score=0.8,
                confidence=0.7,
            )
        ],
        limitations=["模式 B 仍为预留结构"],
        generated_at="2026-09-11T10:01:00+08:00",
    )

    assert result.event_query_id == "query-1"
    assert result.retrieved_evidence[0].platform == "weibo"
