"""Client and normalization helpers for Zhihu Data Open Platform APIs."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings

UTC = timezone.utc


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


class ZhihuHotListResult(BaseModel):
    total: int | None = None
    fetched_at: datetime
    items: list[ZhihuHotListItem]
    raw_payload: dict[str, Any]


class ZhihuSearchResult(BaseModel):
    fetched_at: datetime
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
        items_payload = _extract_items(data)
        items = [ZhihuHotListItem.model_validate(item) for item in items_payload]
        return ZhihuHotListResult(
            total=_extract_total(data),
            fetched_at=datetime.now(UTC),
            items=items,
            raw_payload=payload,
        )

    def search(self, query: str, count: int = 5) -> ZhihuSearchResult:
        safe_count = max(1, min(count, 20))
        payload = self._get(
            self.config.search_path,
            params={"Query": query, "Count": safe_count},
        )
        return ZhihuSearchResult(fetched_at=datetime.now(UTC), raw_payload=payload)

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
            response = client.get(path, params=params, headers=self._headers())
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

        identity = item.url or item.title or f"zhihu-hotlist-{result.fetched_at.isoformat()}-{index}"
        content_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        normalized.append(
            {
                "source_id": "zhihu_hot_list",
                "source_name": "知乎热榜",
                "source_type": "community_hotlist",
                "source_origin": "official_api",
                "source_status": "use",
                "platform": "zhihu",
                "signal_role": "discussion_focus_signal",
                "score_contribution_role": ["discussion", "attention", "community_heat"],
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
                    "rank": index,
                    "total": result.total,
                    "hot_value": None,
                },
                "normalized": {
                    "rank": index,
                    "thumbnail_url": item.thumbnail_url,
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


def _unwrap_payload(payload: dict[str, Any]) -> Any:
    for key in ("Data", "data"):
        if key in payload:
            return payload[key]
    return payload


def _extract_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("Items", "items", "List", "list", "Results", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _extract_total(data: Any) -> int | None:
    if not isinstance(data, dict):
        return None
    value = data.get("Total", data.get("total"))
    return value if isinstance(value, int) else None


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
