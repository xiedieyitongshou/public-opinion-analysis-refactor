"""Minimal Weibo heat client using RSSHub seeds and optional Weibo CLI enrichment."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, ConfigDict

from app.core.config import settings

USER_AGENT = "public-opinion-analysis-weibo-heat-minimal/0.1"
INVALID_XML_CHARS = re.compile(
    "[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)
BARE_AMPERSAND = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)")
HTML_TAG = re.compile(r"<[^>]+>")
HOT_VALUE = re.compile(r"(?<!\d)(\d{4,})(?!\d)")


class WeiboHeatError(RuntimeError):
    """Raised when the minimal Weibo heat source cannot return RSSHub topics."""


class WeiboRSSHubItem(BaseModel):
    """One Weibo hot topic parsed from RSSHub."""

    rank: int
    title: str | None
    url: str | None
    description: str | None
    published_at: str | None
    guid: str | None
    hot_value: int | None
    quality_flags: list[str]


class WeiboCLIStatusSample(BaseModel):
    """A small normalized sample from `weibo-cli search statuses/limited`."""

    model_config = ConfigDict(extra="allow")

    external_id: str | None
    mid: str | None
    text: str | None
    url: str | None
    author: str | None
    created_at: str | None
    repost_count: int | None
    comment_count: int | None
    like_count: int | None
    quality_flags: list[str]


class WeiboCLIEnrichment(BaseModel):
    """CLI enrichment result for one RSSHub topic."""

    enabled: bool
    available: bool
    command: list[str]
    returncode: int | None
    stderr: str | None
    total_number_proxy: int | None
    samples: list[WeiboCLIStatusSample]
    raw_payload: Any | None = None
    quality_flags: list[str]


class WeiboHeatTopic(BaseModel):
    """Unified minimal heat topic for downstream scoring."""

    source_id: str
    source_name: str
    source_type: str
    source_origin: str
    source_status: str
    platform: str
    signal_role: str
    signal_contribution_role: list[str]
    external_id: str
    content_hash: str
    topic: str | None
    title: str | None
    url: str | None
    summary: str | None
    published_at: str | None
    fetched_at: str
    raw_metrics: dict[str, Any]
    normalized: dict[str, Any]
    quality_flags: list[str]
    cli_enrichment: WeiboCLIEnrichment | None


class WeiboHeatResult(BaseModel):
    """Top-level result from the minimal Weibo heat collector."""

    generated_at: str
    rsshub: dict[str, Any]
    cli: dict[str, Any]
    items: list[WeiboHeatTopic]


@dataclass(frozen=True)
class WeiboHeatClientConfig:
    rsshub_base_url: str = settings.weibo_rsshub_base_url
    rsshub_route: str = settings.weibo_rsshub_route
    rsshub_fetch_limit: int = settings.weibo_rsshub_fetch_limit
    rsshub_skip_top: int = settings.weibo_rsshub_skip_top
    rsshub_timeout_seconds: float = settings.weibo_rsshub_timeout_seconds
    rsshub_max_retries: int = settings.weibo_rsshub_max_retries
    cli_enabled: bool = settings.weibo_cli_enabled
    cli_command: str = settings.weibo_cli_command
    cli_topic_limit: int = settings.weibo_cli_topic_limit
    cli_search_count: int = settings.weibo_cli_search_count
    cli_timeout_seconds: float = settings.weibo_cli_timeout_seconds


@dataclass(frozen=True)
class CLIProcessResult:
    returncode: int
    stdout: str
    stderr: str


CLIRunner = Callable[[list[str], float], CLIProcessResult]


class WeiboHeatClient:
    """Fetches minimal Weibo heat data without cookies or crawler bypasses."""

    def __init__(
        self,
        config: WeiboHeatClientConfig | None = None,
        http_client: httpx.Client | None = None,
        cli_runner: CLIRunner | None = None,
    ) -> None:
        self.config = config or WeiboHeatClientConfig()
        self._client = http_client
        self._cli_runner = cli_runner or run_cli_command

    def fetch_heat(
        self,
        *,
        limit: int | None = None,
        skip_top: int | None = None,
        with_cli: bool | None = None,
        include_cli_raw: bool = False,
    ) -> WeiboHeatResult:
        safe_limit = _clamp(limit or self.config.rsshub_fetch_limit, 1, 50)
        safe_skip_top = _clamp(
            self.config.rsshub_skip_top if skip_top is None else skip_top,
            0,
            safe_limit - 1,
        )
        rsshub_url = build_url(self.config.rsshub_base_url, self.config.rsshub_route)
        fetched_at = datetime.now(UTC).isoformat()
        rsshub_items, rsshub_meta = self._fetch_rsshub_topics(
            rsshub_url,
            limit=safe_limit,
            fetched_at=fetched_at,
        )
        selected_items = rsshub_items[safe_skip_top:]
        cli_should_run = self.config.cli_enabled if with_cli is None else with_cli
        cli_topic_limit = min(self.config.cli_topic_limit, len(selected_items))

        topics: list[WeiboHeatTopic] = []
        for index, item in enumerate(selected_items, start=1):
            cli_enrichment = None
            if cli_should_run and index <= cli_topic_limit:
                cli_enrichment = self.search_with_cli(
                    item.title or "",
                    include_raw=include_cli_raw,
                )
            topics.append(
                normalize_heat_topic(
                    item,
                    fetched_at=fetched_at,
                    cli_enrichment=cli_enrichment,
                )
            )

        return WeiboHeatResult(
            generated_at=fetched_at,
            rsshub={
                **rsshub_meta,
                "base_url": self.config.rsshub_base_url,
                "route": self.config.rsshub_route,
                "url": rsshub_url,
                "requested_limit": safe_limit,
                "skip_top": safe_skip_top,
                "selected_count": len(selected_items),
            },
            cli={
                "enabled": cli_should_run,
                "command": split_command(self.config.cli_command),
                "topic_limit": cli_topic_limit,
                "search_count": self.config.cli_search_count,
                "timeout_seconds": self.config.cli_timeout_seconds,
                "raw_payload_included": include_cli_raw,
            },
            items=topics,
        )

    def search_with_cli(self, query: str, *, include_raw: bool = False) -> WeiboCLIEnrichment:
        base_command = split_command(self.config.cli_command)
        command = [
            *base_command,
            "search",
            "statuses/limited",
            "--q",
            query,
            "--type",
            "1",
            "--count",
            str(max(10, self.config.cli_search_count)),
            "--output",
            "json",
        ]
        if not query.strip():
            return WeiboCLIEnrichment(
                enabled=True,
                available=False,
                command=command,
                returncode=None,
                stderr="Empty query.",
                total_number_proxy=None,
                samples=[],
                quality_flags=["empty_query"],
            )

        try:
            result = self._cli_runner(command, self.config.cli_timeout_seconds)
        except FileNotFoundError:
            return WeiboCLIEnrichment(
                enabled=True,
                available=False,
                command=command,
                returncode=None,
                stderr=f"CLI command not found: {base_command[0]}",
                total_number_proxy=None,
                samples=[],
                quality_flags=["weibo_cli_not_installed"],
            )
        except subprocess.TimeoutExpired:
            return WeiboCLIEnrichment(
                enabled=True,
                available=False,
                command=command,
                returncode=None,
                stderr="CLI command timed out.",
                total_number_proxy=None,
                samples=[],
                quality_flags=["weibo_cli_timeout"],
            )

        if result.returncode != 0:
            return WeiboCLIEnrichment(
                enabled=True,
                available=False,
                command=command,
                returncode=result.returncode,
                stderr=result.stderr.strip() or None,
                total_number_proxy=None,
                samples=[],
                quality_flags=[classify_cli_failure(result.stderr)],
            )

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return WeiboCLIEnrichment(
                enabled=True,
                available=False,
                command=command,
                returncode=result.returncode,
                stderr="CLI stdout was not valid JSON.",
                total_number_proxy=None,
                samples=[],
                quality_flags=["weibo_cli_invalid_json"],
            )

        status_payloads = extract_status_items(payload)
        samples = [
            normalize_cli_status(status)
            for status in status_payloads[: self.config.cli_search_count]
        ]
        flags: list[str] = []
        if not samples:
            flags.append("weibo_cli_no_status_samples")

        return WeiboCLIEnrichment(
            enabled=True,
            available=True,
            command=command,
            returncode=result.returncode,
            stderr=result.stderr.strip() or None,
            total_number_proxy=extract_total_number(payload),
            samples=samples,
            raw_payload=payload if include_raw else None,
            quality_flags=flags,
        )

    def _fetch_rsshub_topics(
        self,
        rsshub_url: str,
        *,
        limit: int,
        fetched_at: str,
    ) -> tuple[list[WeiboRSSHubItem], dict[str, Any]]:
        close_client = False
        client = self._client
        if client is None:
            timeout = httpx.Timeout(self.config.rsshub_timeout_seconds, connect=10.0)
            client = httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})
            close_client = True

        response: httpx.Response | None = None
        last_error: str | None = None
        attempts = max(1, self.config.rsshub_max_retries + 1)
        try:
            for _ in range(attempts):
                try:
                    response = client.get(
                        rsshub_url,
                        follow_redirects=True,
                        headers={"Accept": "application/rss+xml, application/xml, text/xml"},
                    )
                    response.raise_for_status()
                    items = parse_rsshub_items(response.text, limit=limit)
                    return items, {
                        "fetched_at": fetched_at,
                        "http_status": response.status_code,
                        "content_type": response.headers.get("content-type"),
                        "response_bytes": len(response.content),
                        "attempts": attempts,
                        "parse_ok": True,
                        "item_count": len(items),
                        "error": None,
                    }
                except (httpx.HTTPError, ET.ParseError, ValueError) as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
        finally:
            if close_client:
                client.close()

        raise WeiboHeatError(
            f"RSSHub Weibo hot route unavailable after {attempts} attempts: {last_error}"
        )


def run_cli_command(command: list[str], timeout_seconds: float) -> CLIProcessResult:
    resolved_command = list(command)
    executable = shutil.which(resolved_command[0])
    if executable is not None:
        resolved_command[0] = executable
    process = subprocess.run(
        resolved_command,
        capture_output=True,
        check=False,
        encoding="utf-8",
        env=os.environ.copy(),
        errors="replace",
        timeout=timeout_seconds,
    )
    return CLIProcessResult(
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )


def split_command(command: str) -> list[str]:
    stripped = command.strip()
    if not stripped:
        return ["weibo-cli"]
    return shlex.split(stripped, posix=os.name != "nt")


def build_url(base_url: str, route: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", route.lstrip("/"))


def parse_rsshub_items(xml_text: str, *, limit: int) -> list[WeiboRSSHubItem]:
    root = ET.fromstring(clean_xml(xml_text))
    parsed_items: list[WeiboRSSHubItem] = []
    for rank, item in enumerate(root.findall(".//item")[:limit], start=1):
        title = element_text(item, "title")
        url = element_text(item, "link")
        description = plain_text(element_text(item, "description"))
        published_at = parse_datetime(element_text(item, "pubDate"))
        guid = element_text(item, "guid")
        hot_value = parse_hot_value(title, description)
        flags = ["list_position_derived_from_rss_order", "not_heat_metric"]
        if not title:
            flags.append("missing_title")
        if not url:
            flags.append("missing_url")
        if not description:
            flags.append("missing_description")
        if not published_at:
            flags.append("missing_pub_date")
        if hot_value is None:
            flags.append("missing_hot_value")

        parsed_items.append(
            WeiboRSSHubItem(
                rank=rank,
                title=title,
                url=url,
                description=description,
                published_at=published_at,
                guid=guid,
                hot_value=hot_value,
                quality_flags=flags,
            )
        )
    return parsed_items


def normalize_heat_topic(
    item: WeiboRSSHubItem,
    *,
    fetched_at: str,
    cli_enrichment: WeiboCLIEnrichment | None,
) -> WeiboHeatTopic:
    identity = item.guid or item.url or item.title or f"weibo-rsshub-{fetched_at}-{item.rank}"
    content_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    flags = list(item.quality_flags)
    matched_status_count = None
    search_total_proxy = None
    top_repost_count = None
    top_comment_count = None
    top_like_count = None
    signal_status = "rsshub_only"

    if cli_enrichment is not None:
        flags.extend(cli_enrichment.quality_flags)
        if cli_enrichment.available:
            signal_status = "rsshub_plus_cli"
            matched_status_count = len(cli_enrichment.samples)
            search_total_proxy = cli_enrichment.total_number_proxy
            top_repost_count = max_optional(
                sample.repost_count for sample in cli_enrichment.samples
            )
            top_comment_count = max_optional(
                sample.comment_count for sample in cli_enrichment.samples
            )
            top_like_count = max_optional(sample.like_count for sample in cli_enrichment.samples)
        else:
            signal_status = "cli_unavailable"

    return WeiboHeatTopic(
        source_id="weibo_rsshub_hot_search",
        source_name="微博热搜 RSSHub",
        source_type="community_hotlist",
        source_origin="rsshub",
        source_status="use",
        platform="weibo",
        signal_role="topic_discovery_signal",
        signal_contribution_role=["weak_attention_seed"],
        external_id=content_hash[:24],
        content_hash=content_hash,
        topic=item.title,
        title=item.title,
        url=item.url,
        summary=item.description,
        published_at=item.published_at,
        fetched_at=fetched_at,
        raw_metrics={
            "list_position": item.rank,
            "hot_value": item.hot_value,
            "weibo_search_total_number_proxy": search_total_proxy,
            "matched_status_count": matched_status_count,
            "top_status_repost_count": top_repost_count,
            "top_status_comment_count": top_comment_count,
            "top_status_like_count": top_like_count,
        },
        normalized={
            "weibo_signal_status": signal_status,
            "weibo_list_position_source": "rss_item_order",
            "topic_present": item.title is not None,
        },
        quality_flags=dedupe(flags),
        cli_enrichment=cli_enrichment,
    )


def normalize_cli_status(status: dict[str, Any]) -> WeiboCLIStatusSample:
    user = status.get("user") if isinstance(status.get("user"), dict) else {}
    text = first_present(status, "text", "text_raw", "content")
    status_id = first_present(status, "idstr", "id", "mid")
    mid = first_present(status, "mid")
    url = first_present(status, "url", "scheme")
    flags: list[str] = []
    if not status_id:
        flags.append("missing_id")
    if not text:
        flags.append("missing_text")
    if not first_present(status, "created_at", "createdAt"):
        flags.append("missing_created_at")

    return WeiboCLIStatusSample(
        external_id=str(status_id) if status_id is not None else None,
        mid=str(mid) if mid is not None else None,
        text=str(text) if text is not None else None,
        url=str(url) if url is not None else None,
        author=str(first_present(user, "screen_name", "name")) if user else None,
        created_at=normalize_created_at(first_present(status, "created_at", "createdAt")),
        repost_count=as_int(first_present(status, "reposts_count", "repost_count")),
        comment_count=as_int(first_present(status, "comments_count", "comment_count")),
        like_count=as_int(first_present(status, "attitudes_count", "like_count", "likes_count")),
        quality_flags=flags,
    )


def extract_status_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if looks_like_status(item)]
    if not isinstance(payload, dict):
        return []
    for key in ("statuses", "items", "Items", "list", "List", "results", "Results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if looks_like_status(item)]
    for key in ("data", "Data"):
        value = payload.get(key)
        if value is not payload:
            nested = extract_status_items(value)
            if nested:
                return nested
    return [payload] if looks_like_status(payload) else []


def extract_total_number(payload: Any) -> int | None:
    if isinstance(payload, dict):
        for key in ("total_number", "total", "Total", "count", "Count"):
            value = as_int(payload.get(key))
            if value is not None:
                return value
        for key in ("data", "Data"):
            value = payload.get(key)
            if value is not payload:
                nested = extract_total_number(value)
                if nested is not None:
                    return nested
    return None


def classify_cli_failure(stderr: str) -> str:
    text = stderr.lower()
    if "缺少登录令牌" in stderr or "auth login" in text or "token" in text:
        return "weibo_cli_missing_token"
    if "401" in text or "unauthorized" in text:
        return "weibo_cli_unauthorized"
    if "quota" in text or "额度" in stderr:
        return "weibo_cli_quota_unavailable"
    if "permission" in text or "权限" in stderr:
        return "weibo_cli_permission_denied"
    return "weibo_cli_command_failed"


def looks_like_status(value: Any) -> bool:
    status_keys = ("text", "text_raw", "id", "idstr", "mid")
    return isinstance(value, dict) and any(key in value for key in status_keys)


def clean_xml(text: str) -> str:
    text = INVALID_XML_CHARS.sub("", text)
    return BARE_AMPERSAND.sub("&amp;", text)


def element_text(element: ET.Element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def plain_text(value: str | None) -> str | None:
    if not value:
        return None
    text = HTML_TAG.sub(" ", value)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def parse_datetime(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return value.strip() or None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def parse_hot_value(title: str | None, description: str | None) -> int | None:
    text = f"{title or ''} {description or ''}"
    matches = [int(match) for match in HOT_VALUE.findall(text)]
    return max(matches) if matches else None


def first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def normalize_created_at(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return parse_datetime(value) or value
    return str(value)


def max_optional(values: Any) -> int | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))
