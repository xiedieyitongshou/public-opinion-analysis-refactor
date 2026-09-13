import pytest
from pydantic import ValidationError

from app.schemas import (
    EventSignal,
    EventSignalTraceabilityCheck,
    ExtractEventSignalsInput,
    NormalizedItem,
    SourceSignal,
)
from app.tools import default_tool_registry


def event_signal_payload() -> dict[str, object]:
    return {
        "event_signal_id": "event-sig-1",
        "source_signal_ids": ["source-sig-1"],
        "title": "sample event",
        "keywords": ["sample", "event"],
        "entities": ["sample entity"],
        "event_type": "public_issue",
        "action_terms": ["reported"],
        "event_text_for_match": "sample entity reported sample event",
        "semantic_fingerprint": {
            "event_text_for_embedding": "sample entity reported sample event",
            "embedding_quality_flags": [],
        },
        "confidence": 0.82,
        "source_statuses": ["use"],
        "source_roles": ["event_signal"],
        "platforms": ["zhihu"],
        "source_signal_count": 99,
        "quality_flags": [],
    }


def source_signal(*, audit_only: bool = False, item_id: str | None = "item-1") -> SourceSignal:
    return SourceSignal(
        source_signal_id="source-sig-1",
        item_id=item_id,
        source_id="zhihu_hot_list",
        source_type="community_hotlist",
        source_status="use",
        source_origin="official_api",
        platform="zhihu",
        signal_role="event_signal",
        title="sample event",
        url="https://www.zhihu.com/question/1",
        fetched_at="2026-09-12T10:00:00+08:00",
        audit_only=audit_only,
        contributes_to_classification=False if audit_only else True,
    )


def normalized_item(*, with_citation: bool = True) -> NormalizedItem:
    return NormalizedItem(
        source_id="zhihu_hot_list",
        source_name="zhihu hot list",
        source_type="community_hotlist",
        source_status="use",
        source_origin="official_api",
        platform="zhihu",
        signal_role="event_signal",
        title="sample event",
        url="https://www.zhihu.com/question/1" if with_citation else None,
        fetched_at="2026-09-12T10:00:00+08:00",
        source_citation=(
            {"item_id": "item-1", "url": "https://www.zhihu.com/question/1"}
            if with_citation
            else {"item_id": "item-1"}
        ),
        quality_flags=[] if with_citation else ["missing_url"],
    )


def test_event_signal_count_is_derived_from_source_signal_ids() -> None:
    signal = EventSignal.model_validate(event_signal_payload())

    assert signal.source_signal_count == 1


def test_event_signal_rejects_event_card_or_debug_only_fields() -> None:
    payload = event_signal_payload()
    payload["confidence_level"] = "high"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EventSignal.model_validate(payload)

    payload = event_signal_payload()
    payload["evidence_text"] = "debug excerpt should not be stored on EventSignal"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EventSignal.model_validate(payload)


def test_event_signal_rejects_embedding_text_version_until_vector_versioning_exists() -> None:
    payload = event_signal_payload()
    payload["semantic_fingerprint"] = {
        "event_text_for_embedding": "sample entity reported sample event",
        "embedding_text_version": "v1",
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EventSignal.model_validate(payload)


def test_event_signal_traceability_allows_referenced_citation_chain() -> None:
    check = EventSignalTraceabilityCheck(
        event_signal=EventSignal.model_validate(event_signal_payload()),
        source_signals=[source_signal()],
        normalized_items=[normalized_item()],
    )

    assert check.event_signal.source_signal_ids == ["source-sig-1"]


def test_event_signal_traceability_rejects_missing_source_signal_reference() -> None:
    signal = EventSignal.model_validate(event_signal_payload())

    with pytest.raises(ValidationError, match="source_signal_ids must reference"):
        EventSignalTraceabilityCheck(
            event_signal=signal,
            source_signals=[],
            normalized_items=[normalized_item()],
        )


def test_event_signal_traceability_rejects_audit_only_source_signal() -> None:
    signal = EventSignal.model_validate(event_signal_payload())

    with pytest.raises(ValidationError, match="audit_only SourceSignal"):
        EventSignalTraceabilityCheck(
            event_signal=signal,
            source_signals=[source_signal(audit_only=True)],
            normalized_items=[normalized_item()],
        )


def test_event_signal_traceability_rejects_missing_item_id_or_citation() -> None:
    signal = EventSignal.model_validate(event_signal_payload())

    with pytest.raises(ValidationError, match="must include item_id"):
        EventSignalTraceabilityCheck(
            event_signal=signal,
            source_signals=[source_signal(item_id=None)],
            normalized_items=[normalized_item()],
        )

    with pytest.raises(ValidationError, match="source citation or URL"):
        EventSignalTraceabilityCheck(
            event_signal=signal,
            source_signals=[source_signal()],
            normalized_items=[normalized_item(with_citation=False)],
        )


def test_extract_event_signals_input_requires_source_signals_not_items() -> None:
    payload = {
        "run_id": "run-1",
        "items": [],
        "use_llm": True,
    }

    with pytest.raises(ValidationError, match="source_signals"):
        ExtractEventSignalsInput.model_validate(payload)


def test_extract_event_signals_tool_uses_day37_schema_contract() -> None:
    result = default_tool_registry.call(
        "extract_event_signals",
        {
            "run_id": "run-1",
            "source_signals": [source_signal().model_dump(mode="json")],
            "use_llm": False,
        },
    )

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["run_id"] == "run-1"
    assert result.output["status"] == "not_implemented"
    assert result.output["event_signals"] == []
    assert "implementation_scheduled_day38" in result.output["quality_flags"]


def test_extract_event_signals_tool_rejects_legacy_items_input() -> None:
    result = default_tool_registry.call(
        "extract_event_signals",
        {
            "run_id": "run-1",
            "items": [],
            "use_llm": False,
        },
    )

    assert result.status == "failed"
    assert "Invalid input for extract_event_signals" in str(result.error_message)
