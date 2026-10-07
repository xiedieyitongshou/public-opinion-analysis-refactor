"""Bounded, read-only searches of official sites, with auditable failure semantics."""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Item, Source
from app.schemas.normalized import NormalizedItem
from app.services.request_usage import http_request

ENDPOINTS = {
    "people": "https://search.people.cn/search-platform/front/search",
    "chinanews": "https://sou.chinanews.com.cn/search.do",
}
SOURCE_NAMES = {"people": "人民网定向搜索", "chinanews": "中新网定向搜索"}
DOMAINS = {
    "people": ("people.com.cn", "people.cn"),
    "chinanews": ("chinanews.com.cn", "chinanews.cn", "chinanews.com"),
}


@dataclass
class SearchResult:
    source: str
    query: str
    status: str
    items: list[NormalizedItem] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)
    error: str | None = None
    http_status: int | None = None

    def audit(self):
        return {
            "source": self.source,
            "query": self.query,
            "status": self.status,
            "item_count": len(self.items),
            "quality_flags": self.quality_flags,
            "error": self.error,
            "http_status": self.http_status,
        }


class OfficialSearchClient:
    def __init__(self, client: httpx.Client | None = None):
        self.client = client

    def search(self, query: str, *, source: str, limit: int = 5) -> SearchResult:
        if source not in ENDPOINTS:
            raise ValueError("unsupported official search source")
        query = " ".join(query.split())[:200]
        if not query:
            raise ValueError("official search requires a nonempty query")
        limit = max(1, min(limit, 20))
        client = self.client or httpx.Client(timeout=settings.official_search_timeout_seconds)
        status = None
        try:
            headers = {"User-Agent": "public-opinion-analysis-official-search/0.2"}
            if source == "people":
                response = http_request(
                    client, "official_search_people", "post",
                    ENDPOINTS[source],
                    json={
                        "key": query,
                        "page": 1,
                        "limit": limit,
                        "hasTitle": True,
                        "hasContent": True,
                        "isFuzzy": False,
                        "type": 0,
                        "sortType": 0,
                        "startTime": 0,
                        "endTime": 0,
                    },
                    headers=headers,
                    follow_redirects=True,
                )
            else:
                response = http_request(
                    client, "official_search_chinanews", "get",
                    ENDPOINTS[source], params={"q": query}, headers=headers, follow_redirects=True
                )
            status = response.status_code
            response.raise_for_status()
            records = (
                parse_people(response.json())
                if source == "people"
                else parse_chinanews(response.text)
            )
            items, flags = [], []
            seen = set()
            fetched_at = datetime.now(UTC)
            for record in records[:limit]:
                item = normalize_record(source, query, record, fetched_at)
                if item is None:
                    flags.append("invalid_or_non_official_record")
                elif item.url not in seen:
                    items.append(item)
                    seen.add(item.url)
            outcome = "partial" if flags and items else "failed" if flags else "succeeded"
            return SearchResult(
                source, query, outcome, items, sorted(set(flags)), http_status=status
            )
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            return SearchResult(
                source,
                query,
                "failed",
                error=type(exc).__name__,
                http_status=status,
                quality_flags=["official_query_failed"],
            )
        finally:
            if self.client is None:
                client.close()


def parse_people(payload) -> list[dict]:
    if not isinstance(payload, dict) or str(payload.get("code")) != "0":
        raise ValueError("people search unsuccessful envelope")
    records = (payload.get("data") or {}).get("records")
    return validated_records(records)


def parse_chinanews(text: str) -> list[dict]:
    match = re.search(r"\b(?:var|let|const)\s+docArr\s*=\s*", text)
    if not match:
        raise ValueError("chinanews docArr missing; not an empty result")
    # JSON parsing only: never execute the surrounding site JavaScript.
    records, _ = json.JSONDecoder().raw_decode(text[match.end() :])
    return validated_records(records)


def validated_records(records):
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise ValueError("official search records changed shape")
    return records


def clean(raw) -> str:
    if isinstance(raw, list):
        return " ".join(clean(part) for part in raw)
    if isinstance(raw, dict):
        return ""
    return " ".join(html.unescape(re.sub(r"<[^>]*>", "", str(raw or ""))).split())


def publication_time(raw) -> datetime | None:
    if raw is None or raw == "":
        return None
    try:
        if isinstance(raw, (int, float)) or re.fullmatch(r"\d{10,13}", str(raw)):
            number = float(raw)
            return datetime.fromtimestamp(number / 1000 if number > 1e11 else number, tz=UTC)
        result = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone(timedelta(hours=8)))
        return result.astimezone(UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def normalize_record(source, query, record, fetched_at) -> NormalizedItem | None:
    title, url = clean(record.get("title")), html.unescape(str(record.get("url") or ""))
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (
        not title
        or parsed.scheme not in {"https", "http"}
        or not any(host == domain or host.endswith("." + domain) for domain in DOMAINS[source])
    ):
        return None
    published = publication_time(
        record.get("pubtime") if source == "chinanews" else record.get("displayTime")
    )
    summary = clean(
        record.get("content_without_tag")
        if source == "chinanews"
        else record.get("content") or record.get("contentOriginal")
    )[:1500]
    digest = hashlib.sha256(url.encode()).hexdigest()
    flags = [] if published else ["missing_published_at"]
    return NormalizedItem(
        source_id=f"{source}_search",
        source_name=SOURCE_NAMES[source],
        source_type="official_news",
        source_status="use",
        source_origin="official_search",
        platform=source,
        signal_role="evidence_signal",
        title=title,
        url=url,
        external_id=str(record.get("unique_id") or record.get("id") or digest[:24]),
        content_hash=digest,
        summary=summary,
        published_at=published,
        fetched_at=fetched_at,
        event_text_for_match=f"{title} {summary}",
        language="zh-CN",
        normalized={
            "official_query": query,
            "retrieval_source": source,
            "metric_semantics": "official_evidence_not_public_attention",
        },
        source_citation={
            "url": url,
            "source_name": SOURCE_NAMES[source],
            "source_status": "use",
            "source_type": "official_news",
            "platform": source,
            "citation_role": "official_search_candidate",
        },
        quality_flags=flags,
        signal_contribution_role=["evidence", "authority"],
    )


def persist_search_items(db: Session, items: list[NormalizedItem]) -> list[Item]:
    rows = []
    for item in items:
        source = db.scalar(select(Source).where(Source.name == item.source_name))
        if source is None:
            source = Source(
                name=item.source_name,
                source_type="official_news",
                source_status="use",
                source_origin="official_search",
                platform=item.platform,
                fetch_config_json={"source_id": item.source_id},
            )
            db.add(source)
            db.flush()
        row = db.scalar(select(Item).where(Item.content_hash == item.content_hash))
        if row is None:
            row = Item(source_id=source.id, title=item.title, content_hash=item.content_hash)
            db.add(row)
            for key in (
                "external_id",
                "url",
                "summary",
                "published_at",
                "fetched_at",
                "source_status",
                "source_origin",
                "signal_role",
                "language",
            ):
                setattr(row, key, getattr(item, key))
            row.normalized_json = item.normalized
            row.quality_flags_json = item.quality_flags
            row.signal_contribution_roles_json = item.signal_contribution_role
            db.flush()
            row.source_citation_json = {**item.source_citation, "item_id": str(row.id)}
        rows.append(row)
    db.flush()
    return rows


def enrich_event_support(
    db, event, pool, *, run_id, config, budget: list[int], client=None, window_end=None
):
    """Search only unsupported community events; cache queries and retain their provenance."""
    from app.services.match_documents import as_datetime
    from app.services.official_paths import build_official_query, enrich_support_detail
    from app.services.official_support import match_official_support

    support = match_official_support(event, pool, config, window_end=window_end)
    if support.official_support_status in {"supported", "weak_supported"}:
        return support
    query = build_official_query(event)
    detail = dict(event.event_detail_json or {})
    previous = detail.get("official_search", {})
    previous_time = as_datetime(previous.get("searched_at"))
    now = datetime.now(UTC)
    cache_hit = previous.get("query") == query.query_text and (
        previous.get("run_id") == run_id
        or (
            previous_time
            and (now - previous_time).total_seconds() < settings.official_search_cache_minutes * 60
        )
    )
    if cache_hit:
        audit = {**previous, "cache_hit": True}
        rows = list(db.scalars(select(Item).where(Item.id.in_(audit.get("item_ids", [])))))
    elif budget[0] <= 0:
        support.quality_flags.append("official_query_budget_exhausted")
        support.official_support_detail.quality_flags.append("official_query_budget_exhausted")
        return support
    else:
        budget[0] -= 1
        client = client or OfficialSearchClient()
        results = [
            client.search(query.query_text, source=source, limit=settings.official_search_limit)
            for source in ENDPOINTS
        ]
        rows = persist_search_items(db, [item for result in results for item in result.items])
        audit = {
            "run_id": run_id,
            "query": query.query_text,
            "searched_at": now.isoformat(),
            "cache_hit": False,
            "results": [result.audit() for result in results],
            "item_ids": sorted({row.id for row in rows}),
        }
    support = match_official_support(event, [*(pool or []), *rows], config, window_end=window_end)
    results = audit.get("results", [])
    succeeded = any(result["status"] == "succeeded" for result in results)
    failed = any(result["status"] != "succeeded" for result in results)
    if support.official_support_status not in {"supported", "weak_supported"}:
        support.official_support_status = "not_found" if succeeded else "not_checked"
    if failed:
        support.quality_flags.append("official_query_incomplete")
    support.official_support_detail = enrich_support_detail(support, official_query=query)
    support.official_support_detail.official_query["search_audit"] = audit
    detail["official_search"] = audit
    event.event_detail_json = detail
    db.commit()
    return support
