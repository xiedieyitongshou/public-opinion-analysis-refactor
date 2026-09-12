"""Weibo RSSHub + CLI collector."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.collectors.base import BaseCollector
from app.schemas import CollectorMetadata, CollectorRunConfig, NormalizedItem
from app.services.weibo_heat_client import WeiboHeatClient, WeiboHeatResult, WeiboHeatTopic


def weibo_heat_metadata() -> CollectorMetadata:
    return CollectorMetadata(
        source_id="weibo_rsshub_hot_search",
        source_name="微博热搜 RSSHub",
        source_type="community_hotlist",
        source_status="use",
        source_origin="rsshub",
        platform="weibo",
        signal_role="topic_discovery_signal",
        signal_contribution_role=["weak_attention_seed"],
        default_limit=20,
        max_limit=50,
    )


class WeiboHeatCollector(BaseCollector):
    """Collector for the current Weibo heat chain.

    RSSHub provides topic seeds. Optional Weibo CLI enrichment is constrained to
    known topics and never becomes an independent hot-list source.
    """

    def __init__(self, client: WeiboHeatClient | None = None) -> None:
        super().__init__(weibo_heat_metadata())
        self.client = client or WeiboHeatClient()

    def fetch(self, config: CollectorRunConfig) -> WeiboHeatResult:
        return self.client.fetch_heat(
            limit=config.limit,
            skip_top=_optional_int(config.params.get("skip_top")),
            with_cli=_optional_bool(config.params.get("with_cli")),
            cli_topic_limit=_optional_int(config.params.get("cli_topic_limit")),
            include_cli_raw=bool(config.params.get("include_cli_raw", False)),
        )

    def parse(
        self,
        raw_payload: WeiboHeatResult,
        config: CollectorRunConfig,
    ) -> Sequence[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in raw_payload.items]

    def normalize(
        self,
        raw_items: Sequence[dict[str, Any]],
        config: CollectorRunConfig,
    ) -> Sequence[NormalizedItem]:
        return [
            NormalizedItem.model_validate(_normalize_weibo_topic(item))
            for item in raw_items
        ]


def _normalize_weibo_topic(item: dict[str, Any] | WeiboHeatTopic) -> dict[str, Any]:
    payload = item.model_dump(mode="json") if isinstance(item, WeiboHeatTopic) else dict(item)
    raw_metrics = payload.get("raw_metrics") or {}
    normalized = payload.get("normalized") or {}
    quality_flags = payload.get("quality_flags") or []
    url = payload.get("url")
    source_name = "微博热搜 RSSHub"

    return {
        "source_id": "weibo_rsshub_hot_search",
        "source_name": source_name,
        "source_type": "community_hotlist",
        "source_status": "use",
        "source_origin": "rsshub",
        "platform": "weibo",
        "signal_role": "topic_discovery_signal",
        "signal_contribution_role": ["weak_attention_seed"],
        "external_id": payload.get("external_id"),
        "title": payload.get("title") or payload.get("topic"),
        "url": url,
        "rank": raw_metrics.get("list_position"),
        "author": None,
        "summary": payload.get("summary"),
        "content": None,
        "content_hash": payload.get("content_hash"),
        "language": "zh-CN",
        "published_at": payload.get("published_at"),
        "fetched_at": payload.get("fetched_at"),
        "raw_metrics": raw_metrics,
        "normalized": {
            **normalized,
            "topic": payload.get("topic"),
            "rank_semantics": "rss_item_order_only_not_official_rank",
        },
        "source_citation": {
            "platform": "weibo",
            "source_name": source_name,
            "url": url,
            "source_status": "use",
            "quality_flags": quality_flags,
        },
        "quality_flags": quality_flags,
        "raw_payload": payload,
    }


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return bool(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
