import hashlib

from app.agents import NormalizerAgent
from app.schemas import NormalizedItem, NormalizeRawItemsInput
from app.tools import default_tool_registry


def mapped_item() -> dict[str, object]:
    return {
        "source_id": "zhihu_hot_list",
        "source_name": "Zhihu hot list",
        "source_type": "community_hotlist",
        "source_status": "use",
        "source_origin": "official_api",
        "platform": "zhihu",
        "signal_role": "attention_signal",
        "title": " <b>  Event   A </b> ",
        "url": " https://example.test/question/1 ",
        "summary": "<p>Summary with url https://noise.test/path</p>",
        "content_text": "Line 1\n\n\nLine 2",
        "published_at": "not-a-date",
        "fetched_at": "2026-09-10T10:00:00+08:00",
        "raw_metrics": {},
        "raw_payload": {"guid": "guid-1"},
        "normalized": {"topics": ["#topic-a#"]},
        "quality_flags": ["low_related_search_result"],
    }


def test_normalizer_agent_cleans_fields_and_sets_quality_flags() -> None:
    result = NormalizerAgent().normalize(NormalizeRawItemsInput(items=[mapped_item()]))

    assert result.status == "succeeded"
    assert result.normalized_count == 1
    item = result.items[0]
    assert item.title == "Event A"
    assert item.url == "https://example.test/question/1"
    assert item.summary == "Summary with url"
    assert item.content_text == "Line 1\n\nLine 2"
    assert item.published_at is None
    assert item.raw_metrics == {}
    assert item.raw_payload["raw_title"] == " <b>  Event   A </b> "
    assert item.quality_flags == [
        "low_related_search_result",
        "invalid_published_at",
        "no_raw_metrics",
    ]
    assert item.event_text_for_match is not None
    assert "topic-a" in item.event_text_for_match
    assert item.event_text_for_embedding is not None
    assert "https://" not in item.event_text_for_embedding
    assert "#" not in item.event_text_for_embedding


def test_normalizer_agent_generates_stable_item_dedup_hash_by_priority() -> None:
    result = NormalizerAgent().normalize(NormalizeRawItemsInput(items=[mapped_item()]))

    expected_identity = "zhihu_hot_list|external|guid-1"
    assert result.items[0].content_hash == hashlib.sha256(
        expected_identity.encode("utf-8")
    ).hexdigest()


def test_normalized_item_schema_accepts_day36_event_text_fields() -> None:
    item = mapped_item()
    item["raw_metrics"] = {"rank": 1}
    item["content_hash"] = "x"
    item["event_text_for_match"] = "event match text"
    item["event_text_for_embedding"] = "event embedding text"

    normalized = NormalizedItem.model_validate(item)

    assert normalized.event_text_for_match == "event match text"
    assert normalized.event_text_for_embedding == "event embedding text"


def test_normalize_raw_items_tool_uses_real_normalizer() -> None:
    result = default_tool_registry.call("normalize_raw_items", {"items": [mapped_item()]})

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["status"] == "succeeded"
    assert result.output["normalized_count"] == 1
    assert result.output["items"][0]["title"] == "Event A"


def test_normalize_raw_items_tool_skips_empty_input_for_plan_compatibility() -> None:
    result = default_tool_registry.call("normalize_raw_items", {})

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["status"] == "skipped"
