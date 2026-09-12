"""Zhihu collectors for hot-list discovery and search enrichment."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from app.collectors.base import BaseCollector
from app.schemas import CollectorMetadata, CollectorRunConfig, CrawlValidationResult, NormalizedItem
from app.services.search_enrichment_filter import evaluate_search_enrichment
from app.services.zhihu_client import (
    ZhihuClient,
    ZhihuHotListResult,
    ZhihuSearchItem,
    ZhihuSearchResult,
    normalize_hot_list_items,
    normalize_search_items,
)


def zhihu_hot_list_metadata() -> CollectorMetadata:
    return CollectorMetadata(
        source_id="zhihu_hot_list",
        source_name="知乎热榜",
        source_type="community_question_hotlist",
        source_status="use",
        source_origin="official_api",
        platform="zhihu",
        signal_role="attention_signal",
        signal_contribution_role=["attention", "discussion", "community_hot_candidate"],
        default_limit=30,
        max_limit=30,
    )


def zhihu_search_metadata() -> CollectorMetadata:
    return CollectorMetadata(
        source_id="zhihu_search",
        source_name="知乎搜索",
        source_type="community_search",
        source_status="use",
        source_origin="official_api",
        platform="zhihu",
        signal_role="search_enrichment_signal",
        signal_contribution_role=["discussion", "interaction"],
        default_limit=5,
        max_limit=20,
    )


class ZhihuHotListCollector(BaseCollector):
    """Collector for the Zhihu official hot-list API."""

    def __init__(self, client: ZhihuClient | None = None) -> None:
        super().__init__(zhihu_hot_list_metadata())
        self.client = client or ZhihuClient()

    def fetch(self, config: CollectorRunConfig) -> ZhihuHotListResult:
        return self.client.fetch_hot_list(limit=config.limit)

    def parse(
        self,
        raw_payload: ZhihuHotListResult,
        config: CollectorRunConfig,
    ) -> Sequence[dict[str, Any]]:
        return [item.model_dump(by_alias=True) for item in raw_payload.items]

    def normalize(
        self,
        raw_items: Sequence[dict[str, Any]],
        config: CollectorRunConfig,
    ) -> Sequence[NormalizedItem]:
        fetched_at = _as_datetime(config.params.get("fetched_at")) or datetime.now(UTC)
        result = ZhihuHotListResult(
            total=config.params.get("total"),
            fetched_at=fetched_at,
            items=raw_items,
            raw_payload={"Items": list(raw_items)},
        )
        return [
            NormalizedItem.model_validate(item)
            for item in normalize_hot_list_items(result)
        ]

    def collect(
        self,
        config: CollectorRunConfig,
        *,
        db: Session | None = None,
    ) -> CrawlValidationResult:
        started = perf_counter()
        requested_limit = min(config.limit, self.metadata.max_limit)
        effective_config = config.model_copy(update={"limit": requested_limit})
        try:
            result = self.fetch(effective_config)
            raw_items = list(self.parse(result, effective_config))[:requested_limit]
            normalize_config = effective_config.model_copy(
                update={
                    "params": {
                        **effective_config.params,
                        "total": result.total,
                        "fetched_at": result.fetched_at,
                    }
                }
            )
            normalized_items = [
                item.model_dump(mode="json") for item in self.normalize(raw_items, normalize_config)
            ]
            saved_count = 0
            if not effective_config.dry_run and effective_config.promote_to_event_pool:
                saved_count = self.save(normalized_items, db=db)
            return self._result(
                effective_config,
                status="succeeded",
                returned_count=len(raw_items),
                normalized_items=normalized_items,
                saved_count=saved_count,
                duration_ms=_duration_ms(started),
            )
        except Exception as exc:  # noqa: BLE001 - source failures must stay observable.
            return self._result(
                effective_config,
                status="failed",
                error_message=str(exc),
                duration_ms=_duration_ms(started),
            )


class ZhihuSearchCollector(BaseCollector):
    """Low-frequency Zhihu search-enrichment collector.

    It only runs when the caller provides `query`, `queries`, or `candidates` in
    per-source params. Without an explicit target it skips the run instead of
    acting as an independent hotspot source.
    """

    def __init__(self, client: ZhihuClient | None = None) -> None:
        super().__init__(zhihu_search_metadata())
        self.client = client or ZhihuClient()

    def collect(
        self,
        config: CollectorRunConfig,
        *,
        db: Session | None = None,
    ) -> CrawlValidationResult:
        if not _search_targets(config.params):
            return self._result(
                config,
                status="skipped",
                error_message="zhihu_search requires query, queries, or candidates params.",
            )
        return super().collect(config, db=db)

    def fetch(self, config: CollectorRunConfig) -> list[dict[str, Any]]:
        targets = _search_targets(config.params)
        max_queries = int(config.params.get("max_queries", 5))
        results: list[dict[str, Any]] = []
        for target in targets[:max_queries]:
            result = self.client.search(target["query"], count=config.limit)
            results.append({**target, "result": result})
        return results

    def parse(
        self,
        raw_payload: list[dict[str, Any]],
        config: CollectorRunConfig,
    ) -> Sequence[dict[str, Any]]:
        parsed: list[dict[str, Any]] = []
        for entry in raw_payload:
            result = entry["result"]
            for item in result.items:
                parsed.append(
                    {
                        **item.model_dump(by_alias=True),
                        "_query": result.query,
                        "_fetched_at": result.fetched_at,
                        "_candidate_title": entry["candidate_title"],
                        "_candidate_url": entry.get("candidate_url"),
                        "_candidate_rank": entry.get("candidate_rank", 0),
                    }
                )
        return parsed

    def normalize(
        self,
        raw_items: Sequence[dict[str, Any]],
        config: CollectorRunConfig,
    ) -> Sequence[NormalizedItem]:
        normalized_items: list[NormalizedItem] = []
        for raw_item in raw_items:
            query = raw_item["_query"]
            fetched_at = _as_datetime(raw_item["_fetched_at"]) or datetime.now(UTC)
            candidate_title = raw_item["_candidate_title"]
            candidate_url = raw_item.get("_candidate_url")
            candidate_rank = int(raw_item.get("_candidate_rank") or 0)
            item_payload = {
                key: value for key, value in raw_item.items() if not key.startswith("_")
            }
            result = ZhihuSearchResult(
                query=query,
                fetched_at=fetched_at,
                items=[ZhihuSearchItem.model_validate(item_payload)],
                raw_payload={"Items": [item_payload]},
            )
            normalized = normalize_search_items(
                result,
                candidate_title=candidate_title,
                candidate_url=candidate_url,
                candidate_rank=candidate_rank,
            )[0]
            relation = evaluate_search_enrichment(
                _parent_item(candidate_title, candidate_url, fetched_at),
                normalized,
                source="zhihu_search",
                parent_candidate_id=str(candidate_url or candidate_title),
            )
            normalized["normalized"]["relation"] = relation.model_dump(mode="json")
            if relation.audit_only:
                normalized["quality_flags"] = _dedupe(
                    [*normalized["quality_flags"], *relation.quality_flags]
                )
                normalized["source_citation"]["quality_flags"] = normalized["quality_flags"]
            normalized_items.append(NormalizedItem.model_validate(normalized))
        return normalized_items


def _search_targets(params: dict[str, Any]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    for candidate in params.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        query = str(candidate.get("query") or candidate.get("title") or "").strip()
        if not query:
            continue
        targets.append(
            {
                "query": query,
                "candidate_title": str(candidate.get("title") or query),
                "candidate_url": candidate.get("url"),
                "candidate_rank": candidate.get("rank", 0),
            }
        )
    for query in params.get("queries", []):
        if str(query).strip():
            targets.append(
                {
                    "query": str(query).strip(),
                    "candidate_title": str(query).strip(),
                    "candidate_url": None,
                    "candidate_rank": 0,
                }
            )
    query = str(params.get("query") or "").strip()
    if query:
        targets.append(
            {
                "query": query,
                "candidate_title": str(params.get("candidate_title") or query),
                "candidate_url": params.get("candidate_url"),
                "candidate_rank": params.get("candidate_rank", 0),
            }
        )
    return _dedupe_targets(targets)


def _parent_item(title: str, url: str | None, fetched_at: datetime) -> dict[str, Any]:
    quality_flags = [] if url else ["missing_url"]
    return {
        "source_id": "zhihu_search_parent",
        "source_name": "搜索补强父候选",
        "source_type": "community_question_hotlist",
        "source_status": "use",
        "source_origin": "official_api",
        "platform": "zhihu",
        "signal_role": "attention_signal",
        "title": title,
        "url": url,
        "fetched_at": fetched_at.isoformat(),
        "quality_flags": quality_flags,
    }


def _dedupe_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str | None]] = set()
    result: list[dict[str, Any]] = []
    for target in targets:
        key = (target["query"], target.get("candidate_url"))
        if key in seen:
            continue
        seen.add(key)
        result.append(target)
    return result


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _duration_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)
