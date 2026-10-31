"""Client and normalization helpers for Zhihu Data Open Platform APIs."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.services.request_usage import http_request


class ZhihuAPIError(RuntimeError):
    """Raised when the Zhihu API returns an unavailable or invalid response."""


class ZhihuAuthError(ZhihuAPIError):
    """Raised when the access secret is missing or rejected."""


class ZhihuHotListItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    title: str | None = Field(default=None, alias="Title")
    url: str | None = Field(default=None, alias="Url")
    thumbnail_url: str | None = Field(default=None, alias="ThumbnailUrl")
    summary: str | None = Field(default=None, alias="Summary")
    hot_value: float | None = Field(default=None, alias="HotValue", ge=0)
    heat_text: str | None = Field(default=None, alias="HeatText")
    heat_provider: str | None = Field(default=None, alias="HeatProvider")
    answer_count: int | None = Field(default=None, alias="AnswerCount", ge=0)
    follower_count: int | None = Field(default=None, alias="FollowerCount", ge=0)
    native_rank: int | None = Field(default=None, alias="NativeRank", ge=1)


class ZhihuHotListResult(BaseModel):
    total: int | None = None
    fetched_at: datetime
    items: list[ZhihuHotListItem]
    raw_payload: dict[str, Any]
    provider: str = "official"
    list_complete: bool = True


class ZhihuSearchItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    title: str | None = Field(default=None, alias="Title")
    content_type: str | None = Field(default=None, alias="ContentType")
    content_id: str | int | None = Field(default=None, alias="ContentID")
    content_text: str | None = Field(default=None, alias="ContentText")
    url: str | None = Field(default=None, alias="Url")
    comment_count: int | None = Field(default=None, alias="CommentCount")
    vote_up_count: int | None = Field(default=None, alias="VoteUpCount")
    author_name: str | None = Field(default=None, alias="AuthorName")
    edit_time: str | int | None = Field(default=None, alias="EditTime")
    authority_level: str | int | None = Field(default=None, alias="AuthorityLevel")
    ranking_score: float | int | None = Field(default=None, alias="RankingScore")


class ZhihuSearchResult(BaseModel):
    query: str
    fetched_at: datetime
    items: list[ZhihuSearchItem]
    raw_payload: dict[str, Any]


class ZhihuQuotaResult(BaseModel):
    fetched_at: datetime
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class ZhihuClientConfig:
    access_secret: str | None = settings.zhihu_access_secret
    base_url: str = settings.zhihu_api_base_url
    hot_list_path: str = settings.zhihu_hot_list_path
    search_path: str = settings.zhihu_search_path
    quota_path: str = settings.zhihu_quota_path
    timeout_seconds: float = settings.zhihu_timeout_seconds


class ZhihuClient:
    """Small HTTP client for the first-party Zhihu developer APIs.

    The client intentionally does not know business persistence. It only fetches
    authorized JSON and maps the documented hot list fields into stable models.
    """

    def __init__(
        self,
        config: ZhihuClientConfig | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config or ZhihuClientConfig()
        self._client = http_client

    def fetch_hot_list(self, limit: int | None = None) -> ZhihuHotListResult:
        safe_limit = settings.zhihu_fetch_limit if limit is None else max(1, min(limit, 30))
        payload = self._get(self.config.hot_list_path, params={"Limit": safe_limit})
        data = _unwrap_payload(payload)
        items_payload = _extract_items(data, strict=True)
        items = [ZhihuHotListItem.model_validate(item) for item in items_payload]
        return ZhihuHotListResult(
            total=_extract_total(data),
            fetched_at=datetime.now(UTC),
            items=items,
            raw_payload=payload,
        )

    def fetch_native_hot_list(self, limit: int = 30) -> ZhihuHotListResult:
        """Read RSSHub's Zhihu upstream JSON, retaining display heat and question counts.

        This is a client endpoint, not the documented developer OpenAPI. Never
        send OpenAPI credentials or automatically retry/fall back to a paid API.
        """
        logger = logging.getLogger(__name__)
        url = "https://api.zhihu.com/topstory/hot-lists/total"
        client = self._client or httpx.Client(timeout=self.config.timeout_seconds)
        try:
            response = http_request(
                client, "zhihu_hot_list", "get", url,
                params={"limit": max(1, min(limit, 30)), "reverse_order": 0},
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(rows, list) or not rows:
                raise ZhihuAPIError("zhihu_native_hotlist_invalid_response")
            items = []
            valid = True
            for rank, row in enumerate(rows[:max(1, min(limit, 30))], 1):
                target = row.get("target") if isinstance(row, dict) else None
                if not isinstance(target, dict):
                    valid = False
                    continue
                token = str(target.get("id") or "")
                kind = target.get("type")
                if not re.fullmatch(r"[1-9][0-9]*", token) or kind not in {"question", "article"}:
                    valid = False
                    continue
                link = (f"https://www.zhihu.com/question/{token}" if kind == "question"
                        else f"https://zhuanlan.zhihu.com/p/{token}")
                heat_text = row.get("detail_text")
                heat = parse_zhihu_heat(heat_text)
                items.append(ZhihuHotListItem.model_validate({
                    "Title": target.get("title"), "Url": link,
                    "Summary": target.get("excerpt") or "",
                    "ThumbnailUrl": target.get("thumbnail") or "",
                    "HotValue": heat, "HeatText": heat_text,
                    "HeatProvider": "zhihu_native_hotlist", "NativeRank": rank,
                    "AnswerCount": target.get("answer_count"),
                    "FollowerCount": target.get("follower_count"),
                    "NativePayload": row,
                }))
            logger.info(
                "zhihu_native_hotlist_fetched returned=%d selected=%d heat_available=%d "
                "heat_missing=%d display_units=true search_enrichment_enabled=%s",
                len(rows), len(items), sum(i.hot_value is not None for i in items),
                sum(i.hot_value is None for i in items), settings.zhihu_search_enrichment_enabled,
            )
            return ZhihuHotListResult(
                total=len(rows), fetched_at=datetime.now(UTC), items=items,
                raw_payload=payload, provider="native", list_complete=valid,
            )
        except (httpx.HTTPError, ValueError, ZhihuAPIError) as exc:
            code = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning("zhihu_native_hotlist_failed error_type=%s http_status=%s",
                           type(exc).__name__, code)
            raise ZhihuAPIError(
                f"zhihu_native_hotlist_unavailable:{code or type(exc).__name__}"
            ) from None
        finally:
            if self._client is None:
                client.close()

    def search(self, query: str, count: int = 5) -> ZhihuSearchResult:
        safe_count = max(1, min(count, 20))
        payload = self._get(
            self.config.search_path,
            params={"Query": query, "Count": safe_count},
        )
        items = [
            ZhihuSearchItem.model_validate(item)
            for item in _extract_items(_unwrap_payload(payload))
        ]
        return ZhihuSearchResult(
            query=query,
            fetched_at=datetime.now(UTC),
            items=items,
            raw_payload=payload,
        )

    def fetch_quota(self, api_ids: list[str] | None = None) -> ZhihuQuotaResult:
        ids = api_ids or ["hot_list", "zhihu_search"]
        payload = self._get(self.config.quota_path, params={"APIIDs": ",".join(ids)})
        return ZhihuQuotaResult(fetched_at=datetime.now(UTC), raw_payload=payload)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.config.access_secret:
            raise ZhihuAuthError("ZHIHU_ACCESS_SECRET is not configured.")

        close_client = False
        client = self._client
        if client is None:
            timeout = httpx.Timeout(self.config.timeout_seconds, connect=10.0)
            client = httpx.Client(base_url=self.config.base_url, timeout=timeout)
            close_client = True

        try:
            source = ("zhihu_hot_list" if path == self.config.hot_list_path else
                      "zhihu_quota" if path == self.config.quota_path else "zhihu_search")
            response = http_request(client, source, "get", path,
                                    params=params, headers=self._headers())
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise ZhihuAuthError("Zhihu API authorization failed.") from exc
            raise ZhihuAPIError(f"Zhihu API HTTP error: {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ZhihuAPIError(f"Zhihu API request failed: {exc}") from exc
        finally:
            if close_client:
                client.close()

        _raise_for_api_error(payload)
        return payload

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.access_secret}",
            "X-Request-Timestamp": str(int(time.time())),
            "User-Agent": "public-opinion-analysis-zhihu-client/0.1",
            "Accept": "application/json",
        }


def parse_zhihu_heat(value: Any) -> float | None:
    """Convert the platform display text; missing/unrecognized values stay unknown."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([万亿]?)\s*热度\s*", value)
    if not match:
        return None
    number = float(match[1]) * {"": 1, "万": 10000, "亿": 100000000}[match[2]]
    return number if math.isfinite(number) else None


def normalize_hot_list_items(result: ZhihuHotListResult) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(result.items, start=1):
        quality_flags = []
        if not item.title:
            quality_flags.append("missing_title")
        if not item.url:
            quality_flags.append("missing_url")
        if not item.summary:
            quality_flags.append("missing_summary")
        if item.heat_provider == "zhihu_native_hotlist" and item.hot_value is None:
            quality_flags.append("zhihu_native_heat_missing")

        identity = (
            item.url or item.title or f"zhihu-hotlist-{result.fetched_at.isoformat()}-{index}"
        )
        content_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        normalized.append(
            {
                "source_id": "zhihu_hot_list",
                "source_name": "知乎热榜",
                "source_type": "community_question_hotlist",
                "source_origin": "official_api",
                "source_status": "use",
                "platform": "zhihu",
                "signal_role": "attention_signal",
                "signal_contribution_role": ["attention", "discussion", "community_hot_candidate"],
                "external_id": content_hash[:24],
                "title": item.title,
                "url": item.url,
                "author": None,
                "summary": item.summary,
                "content": None,
                "content_hash": content_hash,
                "language": "zh-CN",
                "published_at": None,
                "fetched_at": result.fetched_at.isoformat(),
                "raw_metrics": {
                    "rank": item.native_rank or index,
                    "total": result.total,
                    "hot_value": item.hot_value,
                    "heat_text": item.heat_text,
                    "heat_provider": item.heat_provider,
                    "answer_count": item.answer_count,
                    "follower_count": item.follower_count,
                },
                "normalized": {
                    "rank": item.native_rank or index,
                    "thumbnail_url": item.thumbnail_url,
                    "heat_provider": item.heat_provider,
                    "heat_precision": "display" if item.hot_value is not None else None,
                },
                "source_citation": {
                    "platform": "zhihu",
                    "source_name": "知乎热榜",
                    "url": item.url,
                    "source_status": "use",
                    "quality_flags": quality_flags,
                },
                "quality_flags": quality_flags,
                "raw_payload": item.model_dump(by_alias=True),
            }
        )
    return normalized


def normalize_search_items(
    result: ZhihuSearchResult,
    *,
    candidate_title: str,
    candidate_url: str | None,
    candidate_rank: int,
    relation: dict[str, Any] | None = None,
    relations_by_identity: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(result.items, start=1):
        quality_flags = []
        if not item.title:
            quality_flags.append("missing_title")
        if not item.url:
            quality_flags.append("missing_url")
        if item.comment_count is None:
            quality_flags.append("missing_comment_count")
        if item.vote_up_count is None:
            quality_flags.append("missing_vote_up_count")
        if item.edit_time is None:
            quality_flags.append("missing_edit_time")
        if item.ranking_score is None:
            quality_flags.append("missing_ranking_score")

        identity = _search_item_identity(item) or result.query
        item_relation = relation
        if relations_by_identity is not None:
            item_relation = relations_by_identity.get(_search_item_identity(item), relation)
        content_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        normalized.append(
            {
                "source_id": "zhihu_search",
                "source_name": "知乎搜索",
                "source_type": "community_search",
                "source_origin": "official_api",
                "source_status": "use",
                "platform": "zhihu",
                "signal_role": "search_enrichment_signal",
                "signal_contribution_role": ["discussion", "interaction"],
                "external_id": str(item.content_id)
                if item.content_id is not None
                else content_hash[:24],
                "title": item.title,
                "url": item.url,
                "author": item.author_name,
                "summary": item.content_text,
                "content": item.content_text,
                "content_hash": content_hash,
                "language": "zh-CN",
                "published_at": None,
                "fetched_at": result.fetched_at.isoformat(),
                "raw_metrics": {
                    "comment_count": item.comment_count,
                    "vote_count": item.vote_up_count,
                    "ranking_score": item.ranking_score,
                    "edit_time": item.edit_time,
                    "authority_level": item.authority_level,
                },
                "normalized": {
                    "search_rank": index,
                    "query": result.query,
                    "content_type": item.content_type,
                    "candidate_title": candidate_title,
                    "candidate_url": candidate_url,
                    "candidate_rank": candidate_rank,
                    "relation": item_relation or {},
                },
                "source_citation": {
                    "platform": "zhihu",
                    "source_name": "知乎搜索",
                    "url": item.url,
                    "source_status": "use",
                    "quality_flags": quality_flags,
                },
                "quality_flags": quality_flags,
                "raw_payload": item.model_dump(by_alias=True),
            }
        )
    return normalized


def _unwrap_payload(payload: dict[str, Any]) -> Any:
    for key in ("Data", "data"):
        if key in payload:
            return payload[key]
    return payload


def _extract_items(data: Any, *, strict: bool = False) -> list[dict[str, Any]]:
    if isinstance(data, list):
        if strict and not all(isinstance(item, dict) for item in data):
            raise ZhihuAPIError("Zhihu hot list contains invalid records")
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("Items", "items", "List", "list", "Results", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return _extract_items(value, strict=strict)
    if strict:
        raise ZhihuAPIError("Zhihu hot list response has no valid item list")
    return []


def _extract_total(data: Any) -> int | None:
    if not isinstance(data, dict):
        return None
    value = data.get("Total", data.get("total"))
    return value if isinstance(value, int) else None


def _search_item_identity(item: ZhihuSearchItem) -> str | None:
    if item.url:
        return item.url
    if item.content_id is not None:
        return str(item.content_id)
    return item.title


def _raise_for_api_error(payload: dict[str, Any]) -> None:
    code = payload.get("Code", payload.get("code"))
    if code in (None, 0, "0"):
        return

    message = str(
        payload.get("Message")
        or payload.get("message")
        or payload.get("Msg")
        or payload.get("msg")
        or "Zhihu API returned an error."
    )
    if str(code) == "20001":
        raise ZhihuAuthError(message)
    raise ZhihuAPIError(f"Zhihu API error {code}: {message}")
