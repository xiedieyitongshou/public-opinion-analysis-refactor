"""Official news RSS collectors."""

from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.collectors.base import BaseCollector
from app.core.config import settings
from app.schemas import CollectorMetadata, CollectorRunConfig, NormalizedItem, SourceStatus
from app.services.weibo_heat_client import BARE_AMPERSAND, INVALID_XML_CHARS

HTML_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class OfficialRSSSourceConfig:
    source_id: str
    source_name: str
    platform: str
    rss_url: str
    channel: str
    source_status: SourceStatus
    authority_weight: float
    default_author: str | None = None


class OfficialRSSCollector(BaseCollector):
    """Generic low-frequency RSS collector for official news evidence sources."""

    def __init__(
        self,
        source_config: OfficialRSSSourceConfig,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.source_config = source_config
        self._client = http_client
        super().__init__(
            CollectorMetadata(
                source_id=source_config.source_id,
                source_name=source_config.source_name,
                source_type="official_news",
                source_status=source_config.source_status,
                source_origin="official_rss",
                platform=source_config.platform,
                signal_role="evidence_signal",
                signal_contribution_role=["evidence", "authority", "coverage"],
                default_limit=settings.official_rss_fetch_limit,
                max_limit=50,
            )
        )

    def fetch(self, config: CollectorRunConfig) -> str:
        url = str(config.params.get("rss_url") or self.source_config.rss_url)
        close_client = False
        client = self._client
        if client is None:
            timeout_seconds = config.timeout_seconds or settings.official_rss_timeout_seconds
            client = httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=10.0))
            close_client = True

        try:
            response = client.get(
                url,
                follow_redirects=True,
                headers={
                    "Accept": "application/rss+xml, application/xml, text/xml",
                    "User-Agent": "public-opinion-analysis-official-rss/0.1",
                },
            )
            response.raise_for_status()
            return response.text
        finally:
            if close_client:
                client.close()

    def parse(self, raw_payload: str, config: CollectorRunConfig) -> Sequence[dict[str, Any]]:
        root = ET.fromstring(_clean_xml(raw_payload))
        raw_items: list[dict[str, Any]] = []
        for index, element in enumerate(root.findall(".//item"), start=1):
            description = _element_text(element, "description")
            raw_items.append(
                {
                    "title": _element_text(element, "title"),
                    "link": _element_text(element, "link"),
                    "pubDate": _element_text(element, "pubDate"),
                    "author": _element_text(element, "author"),
                    "description": description,
                    "guid": _element_text(element, "guid"),
                    "channel": _element_text(element, "category")
                    or self.source_config.channel,
                    "list_position": index,
                    "description_had_html": _has_html(description),
                }
            )
        return raw_items

    def normalize(
        self,
        raw_items: Sequence[dict[str, Any]],
        config: CollectorRunConfig,
    ) -> Sequence[NormalizedItem]:
        fetched_at = datetime.now(UTC).isoformat()
        normalized_items: list[NormalizedItem] = []
        for raw_item in raw_items:
            title = _clean_text(raw_item.get("title"))
            url = _clean_text(raw_item.get("link"))
            published_at = _parse_datetime(raw_item.get("pubDate"))
            author = _clean_text(raw_item.get("author")) or self.source_config.default_author
            summary = _plain_text(raw_item.get("description"))
            quality_flags = _quality_flags(
                title=title,
                url=url,
                published_at=published_at,
                author=author,
                summary=summary,
                description_had_html=bool(raw_item.get("description_had_html")),
                source_status=self.source_config.source_status,
            )
            identity = (
                raw_item.get("guid")
                or url
                or title
                or f"{self.metadata.source_id}-{fetched_at}"
            )
            content_hash = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()
            normalized_items.append(
                NormalizedItem.model_validate(
                    {
                        "source_id": self.metadata.source_id,
                        "source_name": self.metadata.source_name,
                        "source_type": "official_news",
                        "source_status": self.metadata.source_status,
                        "source_origin": "official_rss",
                        "platform": self.metadata.platform,
                        "signal_role": "evidence_signal",
                        "signal_contribution_role": ["evidence", "authority", "coverage"],
                        "external_id": content_hash[:24],
                        "title": title,
                        "url": url,
                        "channel": raw_item.get("channel") or self.source_config.channel,
                        "rank": raw_item.get("list_position"),
                        "author": author,
                        "summary": summary,
                        "content": None,
                        "content_hash": content_hash,
                        "language": "zh-CN",
                        "published_at": published_at,
                        "fetched_at": fetched_at,
                        "raw_metrics": {
                            "news_list_position": raw_item.get("list_position"),
                            "source_authority_weight": self.source_config.authority_weight,
                        },
                        "normalized": {
                            "rss_url": self.source_config.rss_url,
                            "summary_text": summary,
                            "metric_semantics": "official_evidence_not_public_attention",
                        },
                        "source_citation": {
                            "platform": self.metadata.platform,
                            "source_name": self.metadata.source_name,
                            "url": url,
                            "source_status": self.metadata.source_status,
                            "quality_flags": quality_flags,
                        },
                        "quality_flags": quality_flags,
                        "raw_payload": raw_item,
                    }
                )
            )
        return normalized_items


def default_official_collectors() -> list[OfficialRSSCollector]:
    return [
        OfficialRSSCollector(
            OfficialRSSSourceConfig(
                source_id="chinanews_scroll_rss",
                source_name="中国新闻网即时新闻 RSS",
                platform="chinanews",
                rss_url=settings.chinanews_rss_url,
                channel="即时新闻",
                source_status="use",
                authority_weight=0.75,
            )
        ),
        OfficialRSSCollector(
            OfficialRSSSourceConfig(
                source_id="people_politics_rss",
                source_name="人民网时政 RSS",
                platform="people",
                rss_url=settings.people_rss_url,
                channel="时政",
                source_status="use",
                authority_weight=0.9,
                default_author="人民网",
            )
        ),
        OfficialRSSCollector(
            OfficialRSSSourceConfig(
                source_id="xinhua_politics_rss",
                source_name="新华网时政 RSS",
                platform="xinhua",
                rss_url=settings.xinhua_rss_url,
                channel="时政",
                source_status="fallback",
                authority_weight=0.95,
                default_author="新华网",
            )
        ),
    ]


def _clean_xml(text: str) -> str:
    text = INVALID_XML_CHARS.sub("", text)
    return BARE_AMPERSAND.sub("&amp;", text)


def _element_text(element: ET.Element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _plain_text(value: str | None) -> str | None:
    if not value:
        return None
    text = HTML_TAG.sub(" ", value)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_datetime(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _quality_flags(
    *,
    title: str | None,
    url: str | None,
    published_at: str | None,
    author: str | None,
    summary: str | None,
    description_had_html: bool,
    source_status: SourceStatus,
) -> list[str]:
    flags = ["no_raw_metrics"]
    if not title:
        flags.append("missing_title")
    if not url:
        flags.append("missing_url")
    if not published_at:
        flags.append("missing_published_at")
    if not author:
        flags.append("missing_author")
    if not summary:
        flags.append("missing_summary")
    if description_had_html:
        flags.append("html_summary")
    if source_status == "fallback":
        flags.append("fallback_source")
    if _looks_stale(published_at):
        flags.append("stale_feed_candidate")
    return flags


def _looks_stale(published_at: str | None) -> bool:
    if not published_at:
        return False
    try:
        parsed = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(UTC) - parsed.astimezone(UTC)).days > 30


def _has_html(value: str | None) -> bool:
    return bool(value and HTML_TAG.search(value))
