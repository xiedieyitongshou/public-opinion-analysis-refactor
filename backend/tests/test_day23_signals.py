import pytest
from pydantic import ValidationError

from app.schemas import EventCandidate, EventSignal, SourceSignal
from app.services.search_enrichment_filter import evaluate_search_enrichment


def normalized_item(
    *,
    source_id: str,
    source_origin: str,
    signal_role: str,
    title: str,
    url: str,
    platform: str = "zhihu",
    source_type: str = "community_hotlist",
    summary: str | None = None,
    content: str | None = None,
    quality_flags: list[str] | None = None,
) -> dict[str, object]:
    return {
        "source_id": source_id,
        "source_name": source_id,
        "source_type": source_type,
        "source_status": "use",
        "source_origin": source_origin,
        "platform": platform,
        "signal_role": signal_role,
        "title": title,
        "url": url,
        "summary": summary,
        "content": content,
        "fetched_at": "2026-09-11T10:00:00+08:00",
        "raw_metrics": {},
        "quality_flags": quality_flags or [],
    }


def test_source_signal_requires_search_enrichment_attachment() -> None:
    with pytest.raises(ValidationError, match="search_enrichment_signal must attach"):
        SourceSignal(
            source_signal_id="sig-1",
            source_id="zhihu_search",
            source_type="community_search",
            source_status="use",
            source_origin="official_api",
            platform="zhihu",
            signal_role="search_enrichment_signal",
            title="事件 A 搜索结果",
            url="https://www.zhihu.com/question/1/answer/2",
            fetched_at="2026-09-11T10:00:00+08:00",
        )


def test_audit_only_relation_disables_classification_contribution() -> None:
    relation = evaluate_search_enrichment(
        normalized_item(
            source_id="zhihu_hot_list",
            source_origin="official_api",
            signal_role="attention_signal",
            title="事件 A",
            url="https://www.zhihu.com/question/1",
        ),
        normalized_item(
            source_id="zhihu_search",
            source_origin="official_api",
            signal_role="search_enrichment_signal",
            title="无关回答",
            url="https://www.zhihu.com/question/9/answer/1",
            source_type="community_search",
            summary="完全不同的话题",
        ),
        source="zhihu_search",
        parent_candidate_id="candidate-1",
    )

    signal = SourceSignal(
        source_signal_id="sig-1",
        source_id="zhihu_search",
        source_type="community_search",
        source_status="use",
        source_origin="official_api",
        platform="zhihu",
        signal_role="search_enrichment_signal",
        title="无关回答",
        url="https://www.zhihu.com/question/9/answer/1",
        fetched_at="2026-09-11T10:00:00+08:00",
        parent_candidate_id="candidate-1",
        relation_to_parent=relation,
    )

    assert relation.decision == "audit_only"
    assert signal.audit_only is True
    assert signal.contributes_to_classification is False


def test_event_signal_requires_weak_reason() -> None:
    with pytest.raises(ValidationError, match="weak_signal_reason"):
        EventSignal(
            event_signal_id="event-sig-1",
            source_signal_ids=["source-sig-1"],
            title="弱信号",
            event_text_for_match="弱信号",
            confidence=0.4,
            is_weak_signal=True,
        )


def test_event_candidate_counts_rejected_and_audit_relations() -> None:
    accepted = evaluate_search_enrichment(
        normalized_item(
            source_id="zhihu_hot_list",
            source_origin="official_api",
            signal_role="attention_signal",
            title="事件 A",
            url="https://www.zhihu.com/question/1",
        ),
        normalized_item(
            source_id="zhihu_search",
            source_origin="official_api",
            signal_role="search_enrichment_signal",
            source_type="community_search",
            title="事件 A 最新进展",
            url="https://www.zhihu.com/question/1/answer/2",
        ),
        source="zhihu_search",
        parent_candidate_id="candidate-1",
    )
    rejected = evaluate_search_enrichment(
        normalized_item(
            source_id="zhihu_hot_list",
            source_origin="official_api",
            signal_role="attention_signal",
            title="事件 A",
            url="https://www.zhihu.com/question/1",
        ),
        normalized_item(
            source_id="zhihu_search",
            source_origin="official_api",
            signal_role="search_enrichment_signal",
            source_type="community_search",
            title="历史旧闻",
            url="https://www.zhihu.com/question/3/answer/4",
            quality_flags=["historical_content_pollution"],
        ),
        source="zhihu_search",
        parent_candidate_id="candidate-1",
    )

    candidate = EventCandidate(
        event_candidate_id="candidate-1",
        title="事件 A",
        candidate_key="event-a",
        primary_event_signal_id="event-sig-1",
        event_signal_ids=["event-sig-1"],
        source_signal_ids=["source-sig-1"],
        created_from="zhihu_hot_list",
        search_enrichment_relations=[accepted, rejected],
    )

    assert candidate.rejected_enrichment_count == 1
    assert candidate.audit_only_signal_count == 1


def test_zhihu_search_accepts_same_question_id() -> None:
    relation = evaluate_search_enrichment(
        normalized_item(
            source_id="zhihu_hot_list",
            source_origin="official_api",
            signal_role="attention_signal",
            title="某产品发布引发讨论",
            url="https://www.zhihu.com/question/123",
        ),
        normalized_item(
            source_id="zhihu_search",
            source_origin="official_api",
            signal_role="search_enrichment_signal",
            source_type="community_search",
            title="某产品发布有哪些影响",
            url="https://www.zhihu.com/question/123/answer/456",
        ),
        source="zhihu_search",
        parent_candidate_id="candidate-1",
    )

    assert relation.decision == "accepted"
    assert "same_zhihu_question_id" in relation.matched_by
    assert relation.audit_only is False


def test_weibo_cli_accepts_hashtag_match() -> None:
    relation = evaluate_search_enrichment(
        normalized_item(
            source_id="weibo_rsshub_hot_search",
            source_origin="rsshub",
            signal_role="topic_discovery_signal",
            platform="weibo",
            title="太子奶创始人李途纯去世",
            url="https://m.weibo.cn/search?q=topic",
        ),
        normalized_item(
            source_id="weibo_cli_search_statuses_limited",
            source_origin="weibo_cli",
            signal_role="search_enrichment_signal",
            source_type="community_search",
            platform="weibo",
            title="相关微博",
            url="https://m.weibo.cn/status/1",
            content="#太子奶创始人李途纯去世# 多家媒体报道相关消息",
        ),
        source="weibo_cli",
        parent_candidate_id="candidate-1",
    )

    assert relation.decision == "accepted"
    assert "hashtag_match" in relation.matched_by


def test_low_relevance_search_result_is_audit_only() -> None:
    relation = evaluate_search_enrichment(
        normalized_item(
            source_id="weibo_rsshub_hot_search",
            source_origin="rsshub",
            signal_role="topic_discovery_signal",
            platform="weibo",
            title="太子奶创始人李途纯去世",
            url="https://m.weibo.cn/search?q=topic",
        ),
        normalized_item(
            source_id="weibo_cli_search_statuses_limited",
            source_origin="weibo_cli",
            signal_role="search_enrichment_signal",
            source_type="community_search",
            platform="weibo",
            title="无关抽奖",
            url="https://m.weibo.cn/status/2",
            content="转发抽奖，今晚开奖",
        ),
        source="weibo_cli",
        parent_candidate_id="candidate-1",
    )

    assert relation.decision == "audit_only"
    assert relation.audit_only is True
    assert "low_relevance_search_result" in relation.quality_flags
