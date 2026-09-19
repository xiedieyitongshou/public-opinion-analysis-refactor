from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models import Event
from app.schemas import HotspotClassificationInput, NormalizedItem, PlatformPresence
from app.services.event_classification import classify_hotspot
from app.services.official_support import (
    apply_official_support_to_event,
    match_official_support,
)

BASE_TIME = datetime(2026, 9, 19, 10, 0, tzinfo=UTC)


def test_official_support_strong_use_source_is_supported() -> None:
    result = match_official_support(
        _event(),
        [
            _official_item(
                title="国家医保局通报医保支付改革试点进展",
                summary="国家医保局发布医保支付改革试点最新进展。",
                source_status="use",
            )
        ],
    )

    assert result.official_support_status == "supported"
    assert result.official_support_detail.match_quality == "strong"
    assert result.official_references[0].title == "国家医保局通报医保支付改革试点进展"
    assert result.official_references[0].url == "https://people.example/a"
    assert result.official_references[0].source_name == "人民网"
    assert result.official_references[0].published_at == BASE_TIME.isoformat()
    assert "strong_official_match" in result.official_references[0].match_reason
    assert result.official_references[0].match_features["title_containment"] is True
    assert result.official_references[0].raw_metric["read_count"] == 1200


def test_official_support_fallback_source_is_weak_supported() -> None:
    result = match_official_support(
        _event(),
        [
            _official_item(
                title="国家医保局通报医保支付改革试点进展",
                source_status="fallback",
                source_name="新华网",
            )
        ],
    )

    assert result.official_support_status == "weak_supported"
    assert result.official_support_detail.match_quality == "weak"
    assert "fallback_source" in result.quality_flags


def test_official_support_missing_fields_are_weak_supported() -> None:
    result = match_official_support(
        _event(),
        [
            _official_item(
                title="国家医保局通报医保支付改革试点进展",
                url=None,
                quality_flags=["missing_url"],
            )
        ],
    )

    assert result.official_support_status == "weak_supported"
    assert "missing_url" in result.quality_flags


def test_official_support_entity_conflict_is_not_found() -> None:
    result = match_official_support(
        _event(),
        [
            _official_item(
                title="教育部通报高校招生改革安排",
                summary="教育部发布高校招生改革安排。",
                normalized={"entities": ["教育部"], "keywords": ["招生改革"]},
            )
        ],
    )

    assert result.official_support_status == "not_found"
    assert result.official_references == []
    assert "official_support_not_found" in result.quality_flags


def test_official_support_far_time_window_is_weak_supported() -> None:
    result = match_official_support(
        _event(),
        [
            _official_item(
                title="国家医保局通报医保支付改革试点进展",
                published_at=BASE_TIME - timedelta(days=20),
            )
        ],
    )

    assert result.official_support_status == "weak_supported"
    assert result.official_support_detail.freshness_bucket == "stale"
    assert "outside_time_window" in result.quality_flags


def test_official_support_no_official_match_is_not_found() -> None:
    result = match_official_support(
        _event(),
        [
            _official_item(
                title="国家林草局介绍湿地保护进展",
                summary="国家林草局介绍湿地保护。",
                normalized={"entities": ["国家林草局"], "keywords": ["湿地保护"]},
            )
        ],
    )

    assert result.official_support_status == "not_found"
    assert result.official_support_detail.official_source_count == 1


def test_official_support_empty_or_missing_pool_is_not_checked() -> None:
    missing_pool = match_official_support(_event(), None)
    empty_pool = match_official_support(_event(), [])

    assert missing_pool.official_support_status == "not_checked"
    assert "official_pool_not_provided" in missing_pool.quality_flags
    assert empty_pool.official_support_status == "not_checked"
    assert "official_pool_empty" in empty_pool.quality_flags


def test_official_support_result_feeds_hotspot_classification_categories() -> None:
    supported = match_official_support(
        _event(),
        [_official_item(title="国家医保局通报医保支付改革试点进展")],
    )

    category_a = classify_hotspot(
        HotspotClassificationInput(
            platform_presence=PlatformPresence(
                weibo_topn=True,
                zhihu_topn=True,
                official_source=True,
            ),
            official_support_status=supported.official_support_status,
        )
    )
    category_d = classify_hotspot(
        HotspotClassificationInput(
            platform_presence=PlatformPresence(weibo_topn=True, official_source=True),
            official_support_status=supported.official_support_status,
        )
    )
    category_f = classify_hotspot(
        HotspotClassificationInput(
            platform_presence=PlatformPresence(official_source=True),
            official_support_status=supported.official_support_status,
        )
    )

    assert category_a.priority_category == "A_cross_platform_with_official"
    assert category_d.priority_category == "D_single_platform_with_official"
    assert category_f.priority_category == "F_official_only"
    assert category_f.cross_platform_match_type == "official_only"


def test_apply_official_support_to_event_persists_json_fields() -> None:
    event = Event(
        event_id="evt-medical-payment",
        title="国家医保局通报医保支付改革试点进展",
        primary_entity="国家医保局",
        keywords_json=["医保支付改革", "试点"],
        first_seen_at=BASE_TIME,
        last_seen_at=BASE_TIME,
        event_detail_json={},
        source_citations_json=[],
    )
    result = match_official_support(
        event,
        [_official_item(title="国家医保局通报医保支付改革试点进展")],
    )

    apply_official_support_to_event(event, result)

    assert event.event_detail_json["official_support_status"] == "supported"
    assert (
        event.event_detail_json["classification_detail"]["official_support_detail"][
            "match_quality"
        ]
        == "strong"
    )
    assert event.source_citations_json[0]["citation_role"] == "official_support"


def _event() -> dict:
    return {
        "event_id": "evt-medical-payment",
        "title": "国家医保局通报医保支付改革试点进展",
        "summary": "医保支付改革试点范围扩大。",
        "primary_entity": "国家医保局",
        "keywords": ["医保支付改革", "试点", "通报"],
        "event_detail": {
            "action_terms": ["通报"],
            "entities": ["国家医保局"],
        },
        "first_seen_at": BASE_TIME,
        "last_seen_at": BASE_TIME,
    }


def _official_item(
    *,
    title: str,
    summary: str | None = "国家医保局通报医保支付改革试点进展。",
    source_status: str = "use",
    source_name: str = "人民网",
    url: str | None = "https://people.example/a",
    published_at: datetime | None = BASE_TIME,
    normalized: dict | None = None,
    quality_flags: list[str] | None = None,
) -> NormalizedItem:
    normalized_payload = {
        "event_text_for_match": f"{title} {summary or ''}",
        "entities": ["国家医保局"],
        "action_terms": ["通报"],
        "keywords": ["医保支付改革", "试点", "通报"],
    }
    if normalized is not None:
        normalized_payload.update(normalized)
    return NormalizedItem(
        source_id=f"{source_name}_rss",
        source_name=source_name,
        source_type="official_news",
        source_status=source_status,
        source_origin="official_rss",
        platform="people",
        signal_role="evidence_signal",
        title=title,
        url=url,
        fetched_at=BASE_TIME,
        summary=summary,
        published_at=published_at,
        raw_metrics={"read_count": 1200},
        normalized=normalized_payload,
        source_citation={
            "title": title,
            "url": url,
            "source_name": source_name,
        },
        quality_flags=quality_flags or [],
    )
