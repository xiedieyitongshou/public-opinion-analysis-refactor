from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import app.tools.default_tools as default_tools
from app.collectors import BaseCollector, CollectorRegistrationError, CollectorRegistry
from app.schemas import (
    CollectorMetadata,
    CollectorRunConfig,
    FetchSourceItemsInput,
    NormalizedItem,
)
from app.tools import ToolContext, ToolDefinition, ToolRegistry


class FakeCollector(BaseCollector):
    def fetch(self, config: CollectorRunConfig) -> dict[str, Any]:
        return {
            "items": [
                {"title": "Example hotspot", "url": "https://example.test/hotspot"},
                {"title": "Second hotspot", "url": "https://example.test/second"},
            ]
        }

    def parse(self, raw_payload: Any, config: CollectorRunConfig) -> Sequence[dict[str, Any]]:
        return raw_payload["items"]

    def normalize(
        self,
        raw_items: Sequence[dict[str, Any]],
        config: CollectorRunConfig,
    ) -> Sequence[NormalizedItem]:
        fetched_at = datetime.now(UTC).isoformat()
        items = []
        for raw_item in raw_items:
            identity = raw_item["url"]
            content_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            items.append(
                NormalizedItem(
                    source_id=self.metadata.source_id,
                    source_name=self.metadata.source_name,
                    source_type=self.metadata.source_type,
                    source_status=self.metadata.source_status,
                    source_origin=self.metadata.source_origin,
                    platform=self.metadata.platform,
                    signal_role=self.metadata.signal_role,
                    signal_contribution_role=self.metadata.signal_contribution_role,
                    external_id=content_hash[:24],
                    title=raw_item["title"],
                    url=raw_item["url"],
                    fetched_at=fetched_at,
                    content_hash=content_hash,
                    raw_payload=raw_item,
                    quality_flags=[],
                )
            )
        return items


def fake_metadata(source_status: str = "use") -> CollectorMetadata:
    return CollectorMetadata(
        source_id="fake_hotlist",
        source_name="Fake Hotlist",
        source_type="community_hotlist",
        source_status=source_status,
        source_origin="mock",
        platform="mock",
        signal_role="attention_signal",
        signal_contribution_role=["attention"],
        default_limit=2,
        max_limit=10,
    )


def test_collector_registry_rejects_postponed_sources() -> None:
    registry = CollectorRegistry()
    collector = FakeCollector(fake_metadata(source_status="postpone"))

    try:
        registry.register(collector)
    except CollectorRegistrationError as exc:
        assert "Postponed source" in str(exc)
    else:
        raise AssertionError("postpone source was registered")


def test_collector_registry_runs_validation_without_persistence() -> None:
    registry = CollectorRegistry()
    registry.register(FakeCollector(fake_metadata()))

    output = registry.collect(
        FetchSourceItemsInput(source_ids=["fake_hotlist"], limit=1, validate_only=True)
    )

    assert output.status == "succeeded"
    assert output.validate_only is True
    assert output.dry_run is True
    assert output.blocks_auto_analysis is False
    assert len(output.results) == 1
    assert output.results[0].normalized_count == 1
    assert output.results[0].saved_count == 0
    assert output.results[0].field_completeness["title"] == 1.0
    assert output.items[0]["source_id"] == "fake_hotlist"


def test_fetch_source_items_tool_dispatches_registered_collectors(monkeypatch) -> None:
    collector_registry = CollectorRegistry()
    collector_registry.register(FakeCollector(fake_metadata()))
    monkeypatch.setattr(default_tools, "default_collector_registry", collector_registry)

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="fetch_source_items",
            description="test fetch",
            input_model=default_tools.FetchSourceItemsInput,
            output_model=default_tools.FetchSourceItemsOutput,
            handler=default_tools.fetch_source_items_handler,
            has_side_effect=True,
            retryable=False,
        )
    )

    result = registry.call(
        "fetch_source_items",
        {"source_ids": ["fake_hotlist"], "limit": 2, "validate_only": True},
        context=ToolContext(actor="collector_agent"),
    )

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["status"] == "succeeded"
    assert result.output["results"][0]["source_id"] == "fake_hotlist"
    assert len(result.output["items"]) == 2
