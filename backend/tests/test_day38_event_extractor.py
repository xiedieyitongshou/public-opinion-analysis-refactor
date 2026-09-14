import hashlib

from app.agents.event_extractor import (
    DeepSeekEventExtractionClient,
    EventSignalExtractor,
    LLMEventExtractionRefiner,
)
from app.schemas import ExtractEventSignalsInput, SourceSignal
from app.tools import default_tool_registry


def source_signal(
    *,
    source_signal_id: str = "source-sig-1",
    title: str = "某品牌公司发布召回通知引发消费者关注",
    signal_role: str = "event_signal",
    source_type: str = "community_hotlist",
    platform: str = "zhihu",
    audit_only: bool = False,
    contributes_to_classification: bool = True,
    published_at: str | None = "2026-09-13T10:00:00+08:00",
    raw_metrics: dict[str, object] | None = None,
    platform_features: dict[str, object] | None = None,
    quality_flags: list[str] | None = None,
) -> SourceSignal:
    return SourceSignal(
        source_signal_id=source_signal_id,
        item_id=f"item-{source_signal_id}",
        source_id="zhihu_hot_list",
        source_type=source_type,
        source_status="use",
        source_origin="official_api",
        platform=platform,
        signal_role=signal_role,
        title=title,
        url="https://example.test/item/1",
        published_at=published_at,
        fetched_at="2026-09-13T10:05:00+08:00",
        raw_metrics=raw_metrics or {},
        platform_features=platform_features or {},
        audit_only=audit_only,
        contributes_to_classification=contributes_to_classification,
        quality_flags=quality_flags or [],
    )


def test_extract_event_signals_tool_extracts_rule_based_event_signal() -> None:
    result = default_tool_registry.call(
        "extract_event_signals",
        {
            "run_id": "run-38",
            "source_signals": [source_signal().model_dump(mode="json")],
            "use_llm": False,
        },
    )

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["status"] == "succeeded"
    event_signal = result.output["event_signals"][0]
    assert event_signal["event_signal_id"] == stable_id("run-38", "source-sig-1")
    assert event_signal["source_signal_ids"] == ["source-sig-1"]
    assert "召回" in event_signal["action_terms"]
    assert event_signal["event_type"] == "consumer_rights"
    assert event_signal["event_time_hint"] == "2026-09-13T10:00:00+08:00"
    assert event_signal["semantic_fingerprint"]["event_text_for_embedding"]
    assert 0.0 <= event_signal["confidence"] <= 1.0


def test_extract_event_signals_skips_audit_only_source_signal() -> None:
    result = default_tool_registry.call(
        "extract_event_signals",
        {
            "run_id": "run-38",
            "source_signals": [
                source_signal(audit_only=True, contributes_to_classification=False).model_dump(
                    mode="json"
                )
            ],
            "use_llm": False,
        },
    )

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["status"] == "skipped"
    assert result.output["event_signals"] == []
    assert result.output["audit_only_source_signal_ids"] == ["source-sig-1"]


def test_extract_event_signals_marks_short_topic_as_weak_signal() -> None:
    result = EventSignalExtractor().extract(
        ExtractEventSignalsInput(
            run_id="run-38",
            source_signals=[
                source_signal(
                    title="#热搜#",
                    signal_role="topic_discovery_signal",
                    platform="weibo",
                    published_at=None,
                )
            ],
            use_llm=False,
        )
    )

    assert result.status == "succeeded"
    assert result.event_signals[0].is_weak_signal is True
    assert result.event_signals[0].weak_signal_reason in {
        "short_or_generic_topic_without_enough_event_constraints",
        "topic_discovery_signal_without_event_constraints",
    }


def test_extract_event_signals_llm_failure_falls_back_to_rule_result() -> None:
    extractor = EventSignalExtractor(
        use_llm_globally=True,
        refiner=LLMEventExtractionRefiner(client=FailingDeepSeekClient()),
    )

    result = extractor.extract(
        ExtractEventSignalsInput(
            run_id="run-38",
            source_signals=[
                source_signal(
                    title="#短话题#",
                    signal_role="topic_discovery_signal",
                    platform="weibo",
                    published_at=None,
                    platform_features={"rank": 1},
                )
            ],
            use_llm=True,
        )
    )

    assert result.status == "partial"
    assert len(result.event_signals) == 1
    assert "llm_refinement_failed" in result.quality_flags
    assert any("llm refinement failed" in error for error in result.errors)


def test_extract_event_signals_rejects_legacy_items_input() -> None:
    result = default_tool_registry.call(
        "extract_event_signals",
        {
            "run_id": "run-38",
            "items": [],
            "use_llm": False,
        },
    )

    assert result.status == "failed"
    assert "Invalid input for extract_event_signals" in str(result.error_message)


def test_extract_event_signals_empty_input_returns_skipped() -> None:
    result = default_tool_registry.call(
        "extract_event_signals",
        {
            "run_id": "run-38",
            "source_signals": [],
            "use_llm": False,
        },
    )

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["status"] == "skipped"
    assert "empty_source_signals" in result.output["quality_flags"]


def test_event_signal_id_is_stable_for_run_and_source_signal() -> None:
    extractor = EventSignalExtractor()
    input_data = ExtractEventSignalsInput(
        run_id="run-38",
        source_signals=[source_signal(source_signal_id="stable-source")],
        use_llm=False,
    )

    first = extractor.extract(input_data).event_signals[0].event_signal_id
    second = extractor.extract(input_data).event_signals[0].event_signal_id

    assert first == second
    assert first == stable_id("run-38", "stable-source")


class FailingDeepSeekClient(DeepSeekEventExtractionClient):
    def refine(self, prompt_payload: dict[str, object]):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated provider failure")


def stable_id(run_id: str, source_signal_id: str) -> str:
    digest = hashlib.sha256(f"{run_id}:{source_signal_id}".encode()).hexdigest()[:16]
    return f"event-sig-{digest}"
