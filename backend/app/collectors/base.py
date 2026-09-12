"""Base collector contract for source-specific data collectors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Item, Source
from app.schemas import CollectorMetadata, CollectorRunConfig, CrawlValidationResult, NormalizedItem


class CollectorError(RuntimeError):
    """Raised when a collector cannot complete a source run."""


class BaseCollector(ABC):
    """Template method for fetch -> parse -> normalize -> save.

    Concrete collectors only implement source-specific fetch, parse, and
    normalize behavior. The base class handles observability, dry-run behavior,
    field completeness, and optional persistence.
    """

    metadata: CollectorMetadata

    def __init__(self, metadata: CollectorMetadata) -> None:
        self.metadata = metadata

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
            raw_payload = self.fetch(effective_config)
            raw_items = list(self.parse(raw_payload, effective_config))[:requested_limit]
            normalized_items = [
                item.model_dump(mode="json")
                for item in self.normalize(raw_items, effective_config)
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
        except Exception as exc:  # noqa: BLE001 - source failures must be observable.
            return self._result(
                effective_config,
                status="failed",
                error_message=str(exc),
                duration_ms=_duration_ms(started),
            )

    @abstractmethod
    def fetch(self, config: CollectorRunConfig) -> Any:
        """Fetch raw source payload."""

    @abstractmethod
    def parse(self, raw_payload: Any, config: CollectorRunConfig) -> Sequence[dict[str, Any]]:
        """Parse raw source payload into source-shaped item dictionaries."""

    @abstractmethod
    def normalize(
        self,
        raw_items: Sequence[dict[str, Any]],
        config: CollectorRunConfig,
    ) -> Sequence[NormalizedItem]:
        """Normalize parsed source items into canonical `NormalizedItem` objects."""

    def save(self, normalized_items: Sequence[dict[str, Any]], *, db: Session | None) -> int:
        """Persist normalized items when a run is explicitly promoted.

        Day 29 defaults to validation mode, so this method is normally skipped.
        It is still useful as a generic path for concrete collectors added later.
        """

        if db is None:
            return 0

        source = self._get_or_create_source(db)
        saved_count = 0
        for item in normalized_items:
            if not item.get("title") or not item.get("content_hash"):
                continue
            exists = db.scalar(select(Item).where(Item.content_hash == item["content_hash"]))
            if exists is not None:
                continue
            db.add(
                Item(
                    source_id=source.id,
                    external_id=item.get("external_id"),
                    title=item["title"],
                    url=item.get("url"),
                    source_status=item.get("source_status", self.metadata.source_status),
                    source_origin=item.get("source_origin", self.metadata.source_origin),
                    signal_role=item.get("signal_role", self.metadata.signal_role),
                    author=item.get("author"),
                    summary=item.get("summary"),
                    content=item.get("content"),
                    content_hash=item["content_hash"],
                    language=item.get("language"),
                    published_at=_as_datetime(item.get("published_at")),
                    fetched_at=_as_datetime(item.get("fetched_at")),
                    raw_payload_json=item.get("raw_payload"),
                    raw_metrics_json=item.get("raw_metrics"),
                    normalized_json=item.get("normalized"),
                    quality_flags_json=item.get("quality_flags"),
                    signal_contribution_roles_json=item.get("signal_contribution_role"),
                    source_citation_json=item.get("source_citation"),
                )
            )
            saved_count += 1

        db.commit()
        return saved_count

    def _get_or_create_source(self, db: Session) -> Source:
        source = db.scalar(select(Source).where(Source.name == self.metadata.source_name))
        if source is not None:
            return source

        source = Source(
            name=self.metadata.source_name,
            source_type=self.metadata.source_type,
            source_status=self.metadata.source_status,
            source_origin=self.metadata.source_origin,
            platform=self.metadata.platform,
            fetch_limit=self.metadata.default_limit,
            is_active=True,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        return source

    def _result(
        self,
        config: CollectorRunConfig,
        *,
        status: str,
        returned_count: int = 0,
        normalized_items: list[dict[str, Any]] | None = None,
        saved_count: int = 0,
        error_message: str | None = None,
        duration_ms: int | None = None,
    ) -> CrawlValidationResult:
        items = normalized_items or []
        return CrawlValidationResult(
            source_id=self.metadata.source_id,
            source_name=self.metadata.source_name,
            source_status=self.metadata.source_status,
            source_type=self.metadata.source_type,
            source_origin=self.metadata.source_origin,
            platform=self.metadata.platform,
            signal_role=self.metadata.signal_role,
            status=status,
            validate_only=config.validate_only,
            dry_run=config.dry_run,
            requested_limit=config.limit,
            returned_count=returned_count,
            normalized_count=len(items),
            saved_count=saved_count,
            field_completeness=field_completeness(items),
            quality_flags=collect_quality_flags(items),
            error_message=error_message,
            duration_ms=duration_ms,
            normalized_items=items,
        )


def field_completeness(items: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Return per-field presence ratio for fields needed by downstream agents."""

    fields = [
        "source_id",
        "source_name",
        "source_type",
        "source_status",
        "source_origin",
        "platform",
        "signal_role",
        "title",
        "url",
        "fetched_at",
    ]
    if not items:
        return {field: 0.0 for field in fields}
    return {
        field: round(
            sum(1 for item in items if item.get(field) not in (None, "", [])) / len(items),
            4,
        )
        for field in fields
    }


def collect_quality_flags(items: Sequence[dict[str, Any]]) -> list[str]:
    flags: list[str] = []
    seen: set[str] = set()
    for item in items:
        for flag in item.get("quality_flags") or []:
            if flag not in seen:
                seen.add(flag)
                flags.append(flag)
    return flags


def _duration_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _as_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            normalized = value.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return None
