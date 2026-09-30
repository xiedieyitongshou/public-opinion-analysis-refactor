"""One representation for event identity and official-report comparison."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field


class MatchDocument(BaseModel):
    title: str = ""
    summary: str = ""
    entities: list[str] = Field(default_factory=list)
    action_terms: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    event_time: datetime | None = None
    published_at: datetime | None = None
    observed_at: datetime | None = None
    urls: list[str] = Field(default_factory=list)
    canonical_ids: list[str] = Field(default_factory=list)

    @property
    def text(self) -> str:
        # Keep the original description ahead of extracted (possibly incomplete) features.
        return " ".join(dict.fromkeys(filter(None, [self.title, self.summary])))[:6000]


def value(obj: Any, *names: str, default=None):
    for name in names:
        result = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if result is not None:
            return result
    return default


def as_datetime(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        result = (
            raw
            if isinstance(raw, datetime)
            else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        )
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def event_features(event: Any) -> dict:
    """Nested keys are authoritative, including explicitly empty values; support old rows."""
    detail = value(event, "event_detail_json", "event_detail", default={}) or {}
    nested = detail.get("match_features")
    return {**detail, **(nested if isinstance(nested, dict) else {})}


def canonical_ids(urls: list[str]) -> list[str]:
    identities = []
    for url in urls:
        parsed = urlsplit(url or "")
        host = (parsed.hostname or "").lower()
        if host == "zhihu.com" or host.endswith(".zhihu.com"):
            match = re.match(r"/question/(\d+)(?:/|$)", parsed.path)
            if match:
                identities.append("zhihu:question:" + match[1])
    return sorted(set(identities))


def event_document(event: Any) -> MatchDocument:
    features = event_features(event)
    entities = features.get("entities", value(event, "entities", default=[])) or []
    primary = value(event, "primary_entity")
    # Do not resurrect a deliberately empty nested feature from a stale primary_entity.
    if not entities and primary and "entities" not in features:
        entities = [primary]
    citations = value(event, "source_citations_json", "source_citations", default=[]) or []
    urls = list(value(event, "source_urls", default=[]) or [])
    urls += [c["url"] for c in citations if isinstance(c, dict) and c.get("url")]
    direct_url = value(event, "url")
    if direct_url:
        urls.append(direct_url)
    return MatchDocument(
        title=value(event, "title", default="") or "",
        summary=value(event, "summary", default="") or "",
        entities=entities,
        action_terms=features.get("action_terms", value(event, "action_terms", default=[])) or [],
        keywords=value(event, "keywords_json", "keywords", default=[]) or [],
        event_time=as_datetime(features.get("event_time_hint", value(event, "event_time_hint"))),
        published_at=as_datetime(value(event, "published_at")),
        observed_at=as_datetime(value(event, "last_seen_at", "first_seen_at", "fetched_at")),
        urls=sorted(set(urls)),
        canonical_ids=sorted(
            set(canonical_ids(urls) + list(value(event, "canonical_ids", default=[]) or []))
        ),
    )


def item_document(item: Any) -> MatchDocument:
    normalized = value(item, "normalized_json", "normalized", default={}) or {}
    document = event_document(item)
    return document.model_copy(
        update={
            "summary": value(item, "summary", "content_text", "content", default="") or "",
            "entities": normalized.get("entities", value(item, "entities", default=[])) or [],
            "action_terms": normalized.get("action_terms", value(item, "action_terms", default=[]))
            or [],
            "event_time": as_datetime(normalized.get("event_time_hint")),
        }
    )
