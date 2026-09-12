from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.tools.default_tools as default_tools
from app.agents import CollectorAgent, Planner
from app.collectors import BaseCollector, CollectorRegistry
from app.db.init_db import init_db
from app.models import AgentTask, AgentToolCall, CrawlValidationRun, Item
from app.schemas import CollectorMetadata, CollectorRunConfig, NormalizedItem
from app.tools import ToolDefinition, ToolRegistry


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
            content_hash = hashlib.sha256(raw_item["url"].encode("utf-8")).hexdigest()
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


def test_planner_creates_daily_hotspot_collection_plan() -> None:
    plan = Planner().create_daily_hotspot_collection_plan(
        {
            "source_ids": ["fake_hotlist", "weibo_direct_hot_search"],
            "limit": 5,
        }
    )

    assert plan.mode == "daily_hotspot_collection"
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.tool_name == "fetch_source_items"
    assert step.input_json["source_ids"] == ["fake_hotlist"]
    assert step.input_json["validate_only"] is False
    assert step.input_json["dry_run"] is False
    assert step.input_json["promote_to_event_pool"] is True
    assert "weibo_direct_hot_search" in step.input_json["postponed_source_ids"]


def test_planner_creates_crawl_validation_plan_with_small_sample_limits() -> None:
    plan = Planner().create_crawl_validation_plan(
        {
            "zhihu_search_queries": ["sample event"],
            "with_weibo_cli": True,
        }
    )

    assert plan.mode == "crawl_validation"
    step_input = plan.steps[0].input_json
    assert step_input["validate_only"] is True
    assert step_input["dry_run"] is True
    assert step_input["promote_to_event_pool"] is False
    assert step_input["per_source_limits"]["zhihu_hot_list"] == 10
    assert step_input["per_source_limits"]["zhihu_search"] == 3
    assert step_input["per_source_params"]["zhihu_search"]["queries"] == ["sample event"]
    assert step_input["per_source_params"]["weibo_rsshub_hot_search"]["with_cli"] is True


def test_collector_agent_records_validation_run_without_persisting_items(monkeypatch) -> None:
    session = _make_session()
    monkeypatch.setattr(default_tools, "default_collector_registry", _fake_collector_registry())

    result = CollectorAgent(registry=_tool_registry()).run_crawl_validation(
        session,
        {
            "source_ids": ["fake_hotlist"],
            "per_source_limits": {"fake_hotlist": 1},
            "promote_to_event_pool": True,
        },
    )

    validation_run = session.scalar(select(CrawlValidationRun))
    assert result.mode == "crawl_validation"
    assert result.status == "succeeded"
    assert result.crawl_validation_run_id == validation_run.id
    assert validation_run.promote_to_event_pool is False
    assert validation_run.summary_json["validation_summary"]["normalized_count"] == 1
    assert session.scalar(select(Item)) is None
    assert len(session.scalars(select(AgentTask)).all()) == 1
    assert len(session.scalars(select(AgentToolCall)).all()) == 1


def test_collector_agent_promotes_daily_collection_to_item_pool(monkeypatch) -> None:
    session = _make_session()
    monkeypatch.setattr(default_tools, "default_collector_registry", _fake_collector_registry())

    result = CollectorAgent(registry=_tool_registry()).run_daily_hotspot_collection(
        session,
        {
            "source_ids": ["fake_hotlist"],
            "limit": 2,
            "promote_to_event_pool": True,
        },
    )

    items = session.scalars(select(Item)).all()
    assert result.mode == "daily_hotspot_collection"
    assert result.status == "succeeded"
    assert result.fetch_output["promote_to_event_pool"] is True
    assert result.fetch_output["validation_summary"]["saved_count"] == 2
    assert len(items) == 2
    assert items[0].source_status == "use"


def test_registry_skips_postponed_sources_when_scheduler_passes_gate() -> None:
    output = _fake_collector_registry().collect(
        default_tools.FetchSourceItemsInput(
            source_ids=["fake_hotlist", "weibo_direct_hot_search"],
            postponed_source_ids=["weibo_direct_hot_search"],
            limit=1,
            validate_only=True,
        )
    )

    assert output.status == "partial"
    assert output.skipped_source_ids == ["weibo_direct_hot_search"]
    assert output.results[1].source_status == "postpone"
    assert output.validation_summary["skipped_source_count"] == 1


def _fake_metadata() -> CollectorMetadata:
    return CollectorMetadata(
        source_id="fake_hotlist",
        source_name="Fake Hotlist",
        source_type="community_hotlist",
        source_status="use",
        source_origin="mock",
        platform="mock",
        signal_role="attention_signal",
        signal_contribution_role=["attention"],
        default_limit=2,
        max_limit=10,
    )


def _fake_collector_registry() -> CollectorRegistry:
    registry = CollectorRegistry()
    registry.register(FakeCollector(_fake_metadata()))
    return registry


def _tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="fetch_source_items",
            description="test fetch",
            input_model=default_tools.FetchSourceItemsInput,
            output_model=default_tools.FetchSourceItemsOutput,
            handler=default_tools.fetch_source_items_handler,
            has_side_effect=True,
            retryable=True,
            max_retries=1,
        )
    )
    return registry


def _make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    init_db(engine)
    return sessionmaker(bind=engine)()
