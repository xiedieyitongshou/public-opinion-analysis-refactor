from __future__ import annotations

from datetime import UTC, datetime

from app.schemas import (
    EventSignalTraceabilityCheck,
    NormalizedItem,
    OfficialQuery,
)
from app.services.official_paths import (
    normalized_item_to_source_signal,
    run_community_support_path,
    run_official_agenda_path,
)

BASE_TIME = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)


def test_path_a_community_support_returns_enhanced_official_support() -> None:
    result = run_community_support_path(
        _community_event(),
        [
            _official_item(
                source_id="chinanews_search",
                source_name="中国新闻网",
                external_id="doc-1",
            ),
            _official_item(
                source_id="people_politics_rss",
                source_name="人民网",
                external_id="doc-2",
                url="https://people.example/a",
            ),
        ],
        official_query=OfficialQuery(query_text="国家医保局 医保支付改革", event_id="evt-medical"),
    )

    assert result.path_type == "community_support"
    assert result.status == "succeeded"
    assert result.eligible_for_community_main_list is True
    assert result.official_support_result is not None
    assert result.official_support_result.official_support_status == "supported"
    detail = result.official_support_result.official_support_detail
    assert detail.official_item_count == 2
    assert detail.source_coverage_count == 2
    assert detail.unique_story_count == 2
    assert detail.official_coverage_level == "multi_source_independent"
    assert detail.official_query["query_text"] == "国家医保局 医保支付改革"


def test_path_a_requires_existing_community_presence() -> None:
    event = _community_event()
    event["event_detail"]["platform_presence"] = {}

    result = run_community_support_path(event, [_official_item()])

    assert result.status == "skipped"
    assert "community_presence_required" in result.quality_flags


def test_official_item_to_source_signal_adapter_is_traceable() -> None:
    item = _official_item()
    source_signal = normalized_item_to_source_signal(item, path_type="official_agenda")

    assert source_signal.item_id == item.external_id
    assert source_signal.source_type == "official_news"
    assert source_signal.source_origin == "official_rss"
    assert source_signal.signal_role == "event_signal"
    assert source_signal.raw_metrics["read_count"] == 1200
    assert source_signal.classification_features["official_path_type"] == "official_agenda"
    assert source_signal.classification_features["source_citation"]["item_id"] == item.external_id


def test_path_b_official_agenda_reuses_extract_and_match_chain() -> None:
    result = run_official_agenda_path([_official_item()], run_id="day45-test")

    assert result.path_type == "official_agenda"
    assert result.status == "succeeded"
    assert result.eligible_for_community_main_list is False
    assert result.source_signals
    assert result.event_resolutions
    assert result.event_resolutions[0].action == "create"
    assert result.downstream_fields["official_only"] is True
    assert result.official_agenda_rank is not None
    assert result.official_agenda_rank.official_source_count == 1


def test_path_b_traceability_check_accepts_adapter_output() -> None:
    item = _official_item()
    result = run_official_agenda_path([item], run_id="day45-trace")

    EventSignalTraceabilityCheck(
        event_signal=result.event_resolutions
        and _event_signal_from_path_b(item, result.source_signals[0].source_signal_id),
        source_signals=result.source_signals,
        normalized_items=[item],
    )


def test_same_official_item_can_be_reused_by_path_a_and_b_without_double_counting() -> None:
    item = _official_item(external_id="shared-doc", url="https://chinanews.example/shared")
    path_a = run_community_support_path(_community_event(), [item])
    path_b = run_official_agenda_path([item], run_id="day45-shared")

    assert path_a.official_support_result is not None
    assert path_a.official_support_result.official_support_detail.matched_item_ids == ["shared-doc"]
    assert path_a.official_support_result.official_support_detail.unique_story_count == 1
    assert path_b.source_signals[0].item_id == "shared-doc"
    assert path_b.eligible_for_community_main_list is False


def test_path_b_failures_are_mapped_to_official_path_result() -> None:
    result = run_official_agenda_path(
        [
            _official_item(
                title="",
                external_id="bad-doc",
                quality_flags=["missing_title"],
            )
        ],
        run_id="day45-fail",
    )

    assert result.status in {"failed", "skipped"}
    assert result.fallback_used is True
    assert any(flag.startswith("extract_event_signals_") for flag in result.quality_flags)


def _event_signal_from_path_b(item: NormalizedItem, source_signal_id: str):
    from app.agents.event_extractor import EventSignalExtractor
    from app.schemas import ExtractEventSignalsInput

    source_signal = normalized_item_to_source_signal(item, path_type="official_agenda")
    source_signal = source_signal.model_copy(update={"source_signal_id": source_signal_id})
    output = EventSignalExtractor(use_llm_globally=False).extract(
        ExtractEventSignalsInput(
            run_id="day45-trace",
            source_signals=[source_signal],
            use_llm=False,
        )
    )
    return output.event_signals[0]


def _community_event() -> dict:
    return {
        "event_id": "evt-medical",
        "title": "国家医保局通报医保支付改革试点进展",
        "summary": "医保支付改革试点范围扩大。",
        "primary_entity": "国家医保局",
        "keywords": ["医保支付改革", "试点", "通报"],
        "event_detail": {
            "action_terms": ["通报"],
            "entities": ["国家医保局"],
            "platform_presence": {"weibo_topn": True, "zhihu_search": True},
        },
        "first_seen_at": BASE_TIME,
        "last_seen_at": BASE_TIME,
    }


def _official_item(
    *,
    source_id: str = "chinanews_search",
    source_name: str = "中国新闻网",
    external_id: str = "doc-1",
    title: str = "国家医保局通报医保支付改革试点进展",
    url: str = "https://chinanews.example/a",
    quality_flags: list[str] | None = None,
) -> NormalizedItem:
    flags = list(quality_flags or [])
    if not title and "missing_title" not in flags:
        flags.append("missing_title")
    return NormalizedItem(
        source_id=source_id,
        source_name=source_name,
        source_type="official_news",
        source_status="use",
        source_origin="official_rss",
        platform="chinanews",
        signal_role="evidence_signal",
        title=title,
        url=url,
        external_id=external_id,
        fetched_at=BASE_TIME,
        published_at=BASE_TIME,
        summary="国家医保局发布医保支付改革试点最新进展。",
        content_hash=f"hash-{external_id}",
        raw_metrics={"read_count": 1200},
        normalized={
            "event_text_for_match": f"{title} 国家医保局 医保支付改革 试点 通报",
            "entities": ["国家医保局"],
            "action_terms": ["通报"],
            "keywords": ["医保支付改革", "试点", "通报"],
        },
        source_citation={
            "item_id": external_id,
            "title": title,
            "url": url,
            "source_name": source_name,
            "source_type": "official_news",
            "source_status": "use",
            "source_origin": "official_rss",
            "platform": "chinanews",
            "fetched_at": BASE_TIME.isoformat(),
        },
        quality_flags=flags,
    )
