"""Collector registry and dispatch helpers."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.collectors.base import BaseCollector
from app.schemas import (
    CollectorRunConfig,
    CrawlValidationResult,
    FetchSourceItemsInput,
    FetchSourceItemsOutput,
)


class CollectorRegistrationError(RuntimeError):
    """Raised when a collector cannot be registered."""


class CollectorAlreadyRegisteredError(CollectorRegistrationError):
    """Raised when a collector source id is registered more than once."""


class CollectorNotFoundError(CollectorRegistrationError):
    """Raised when a requested collector source id does not exist."""


class CollectorRegistry:
    """Registry for source collectors.

    Registration enforces the Day 29 `source_status` gate: postponed sources are
    documented candidates only and cannot become real collection tools.
    """

    def __init__(self) -> None:
        self._collectors: dict[str, BaseCollector] = {}

    def register(self, collector: BaseCollector) -> None:
        source_id = collector.metadata.source_id
        if collector.metadata.source_status == "postpone":
            raise CollectorRegistrationError(
                f"Postponed source cannot be registered as a real collector: {source_id}"
            )
        if source_id in self._collectors:
            raise CollectorAlreadyRegisteredError(f"Collector already registered: {source_id}")
        self._collectors[source_id] = collector

    def get(self, source_id: str) -> BaseCollector:
        try:
            return self._collectors[source_id]
        except KeyError as exc:
            raise CollectorNotFoundError(f"Collector not found: {source_id}") from exc

    def list_collectors(self) -> list[BaseCollector]:
        return list(self._collectors.values())

    def source_ids(self) -> list[str]:
        return list(self._collectors)

    def collect(
        self,
        input_data: FetchSourceItemsInput,
        *,
        db: Session | None = None,
    ) -> FetchSourceItemsOutput:
        requested_source_ids = input_data.source_ids or self.source_ids()
        results: list[CrawlValidationResult] = []
        unavailable_source_ids: list[str] = []
        skipped_source_ids: list[str] = []
        postponed_source_ids = set(input_data.postponed_source_ids)

        if not requested_source_ids:
            return FetchSourceItemsOutput(
                status="skipped",
                validate_only=input_data.validate_only,
                dry_run=input_data.dry_run,
                promote_to_event_pool=input_data.promote_to_event_pool,
                requested_source_ids=[],
                skipped_source_ids=[],
                blocks_auto_analysis=True,
            )

        for source_id in requested_source_ids:
            if source_id in postponed_source_ids:
                skipped_source_ids.append(source_id)
                results.append(
                    CrawlValidationResult(
                        source_id=source_id,
                        source_status="postpone",
                        status="skipped",
                        requested_limit=_source_limit(input_data, source_id),
                        error_message=f"Postponed source skipped by scheduler: {source_id}",
                    )
                )
                continue

            collector = self._collectors.get(source_id)
            if collector is None:
                unavailable_source_ids.append(source_id)
                results.append(
                    CrawlValidationResult(
                        source_id=source_id,
                        status="failed",
                        requested_limit=_source_limit(input_data, source_id),
                        error_message=f"Collector not found: {source_id}",
                    )
                )
                continue

            config = CollectorRunConfig(
                source_id=source_id,
                limit=_source_limit(input_data, source_id),
                validate_only=input_data.validate_only,
                dry_run=input_data.dry_run,
                promote_to_event_pool=input_data.promote_to_event_pool,
                timeout_seconds=input_data.timeout_seconds,
                max_quota_cost=input_data.max_quota_cost,
                params=input_data.per_source_params.get(source_id, {}),
            )
            result = collector.collect(config, db=db)
            results.append(result)
            if result.status == "failed":
                unavailable_source_ids.append(source_id)
            elif result.status == "skipped":
                skipped_source_ids.append(source_id)

        status = _aggregate_status(results)
        items = [
            item
            for result in results
            for item in result.normalized_items
            if input_data.include_items
        ]
        return FetchSourceItemsOutput(
            status=status,
            validate_only=input_data.validate_only,
            dry_run=input_data.dry_run,
            promote_to_event_pool=input_data.promote_to_event_pool,
            requested_source_ids=requested_source_ids,
            results=results,
            items=items,
            unavailable_source_ids=unavailable_source_ids,
            skipped_source_ids=skipped_source_ids,
            source_status_summary=_source_status_summary(results),
            contribution_roles_by_source=_contribution_roles_by_source(results),
            field_completeness_summary=_field_completeness_summary(results),
            validation_summary=_validation_summary(results),
            blocks_auto_analysis=status in {"failed", "skipped"},
        )


def _aggregate_status(results: list[CrawlValidationResult]) -> str:
    if not results:
        return "skipped"
    succeeded = sum(1 for result in results if result.status == "succeeded")
    failed = sum(1 for result in results if result.status == "failed")
    skipped = sum(1 for result in results if result.status == "skipped")
    if succeeded and failed:
        return "partial"
    if succeeded and skipped:
        return "partial"
    if failed == len(results):
        return "failed"
    if succeeded == len(results):
        return "succeeded"
    return "skipped"


def _source_limit(input_data: FetchSourceItemsInput, source_id: str) -> int:
    return input_data.per_source_limits.get(source_id, input_data.limit)


def _source_status_summary(results: list[CrawlValidationResult]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for result in results:
        key = str(result.source_status)
        summary[key] = summary.get(key, 0) + 1
    return summary


def _contribution_roles_by_source(
    results: list[CrawlValidationResult],
) -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {}
    for result in results:
        contribution_roles: list[str] = []
        for item in result.normalized_items:
            for role in item.get("signal_contribution_role") or []:
                if role not in contribution_roles:
                    contribution_roles.append(role)
        if contribution_roles:
            roles[result.source_id] = contribution_roles
    return roles


def _field_completeness_summary(results: list[CrawlValidationResult]) -> dict[str, float]:
    item_count = sum(result.normalized_count for result in results)
    if item_count == 0:
        return {}

    totals: dict[str, float] = {}
    for result in results:
        for field, ratio in result.field_completeness.items():
            totals[field] = totals.get(field, 0.0) + ratio * result.normalized_count
    return {field: round(total / item_count, 4) for field, total in totals.items()}


def _validation_summary(results: list[CrawlValidationResult]) -> dict[str, int]:
    return {
        "source_count": len(results),
        "succeeded_source_count": sum(1 for result in results if result.status == "succeeded"),
        "failed_source_count": sum(1 for result in results if result.status == "failed"),
        "skipped_source_count": sum(1 for result in results if result.status == "skipped"),
        "returned_count": sum(result.returned_count for result in results),
        "normalized_count": sum(result.normalized_count for result in results),
        "saved_count": sum(result.saved_count for result in results),
    }


def build_default_collector_registry() -> CollectorRegistry:
    registry = CollectorRegistry()

    from app.collectors.official import default_official_collectors
    from app.collectors.weibo import WeiboHeatCollector
    from app.collectors.zhihu import ZhihuHotListCollector, ZhihuSearchCollector

    registry.register(ZhihuHotListCollector())
    registry.register(ZhihuSearchCollector())
    registry.register(WeiboHeatCollector())
    for collector in default_official_collectors():
        registry.register(collector)
    return registry


default_collector_registry = build_default_collector_registry()
