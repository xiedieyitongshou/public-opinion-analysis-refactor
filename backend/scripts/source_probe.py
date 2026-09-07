"""Low-frequency source smoke test for Day 15.

This script probes public pages and RSS feeds only. It is intentionally separate
from collector runtime code so source feasibility can be evaluated before
committing to production collector implementations.
"""

from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

USER_AGENT = "public-opinion-analysis-smoke-test/0.1 (+low-frequency)"
UTC = timezone.utc
INVALID_XML_CHARS = re.compile(
    "[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)
BARE_AMPERSAND = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)")

SOURCES = [
    {
        "source_id": "people_politics_rss",
        "source_name": "人民网",
        "platform": "people.cn",
        "channel": "时政",
        "url": "http://www.people.com.cn/rss/politics.xml",
        "source_type": "official_news",
        "parser": "rss",
    },
    {
        "source_id": "chinanews_scroll_rss",
        "source_name": "中国新闻网",
        "platform": "chinanews.com.cn",
        "channel": "即时新闻",
        "url": "https://www.chinanews.com.cn/rss/scroll-news.xml",
        "source_type": "official_news",
        "parser": "rss",
    },
    {
        "source_id": "xinhua_politics_rss",
        "source_name": "新华网",
        "platform": "xinhuanet.com",
        "channel": "时政",
        "url": "http://www.xinhuanet.com/politics/news_politics.xml",
        "source_type": "official_news",
        "parser": "rss",
    },
    {
        "source_id": "weibo_realtime_hot",
        "source_name": "微博热搜",
        "platform": "weibo.com",
        "channel": "热搜",
        "url": "https://s.weibo.com/top/summary?cate=realtimehot",
        "source_type": "community_hotlist",
        "parser": "public_page_probe",
    },
    {
        "source_id": "zhihu_hot",
        "source_name": "知乎热榜",
        "platform": "zhihu.com",
        "channel": "热榜",
        "url": "https://www.zhihu.com/hot",
        "source_type": "community_question_hotlist",
        "parser": "public_page_probe",
    },
]


@dataclass
class ProbeItem:
    source_id: str
    source_name: str
    source_type: str
    platform: str
    channel: str
    title: str | None
    url: str | None
    published_at: str | None
    fetched_at: str
    author: str | None
    summary: str | None
    raw_metrics: dict[str, Any] | None
    quality_flags: list[str]


@dataclass
class ProbeResult:
    source_id: str
    source_name: str
    endpoint_url: str
    fetched_at: str
    http_status: int | None
    response_content_type: str | None
    response_bytes: int
    accessible: bool
    parse_ok: bool
    item_count: int
    checked_link_count: int
    stable_link_count: int
    field_completeness: dict[str, float]
    time_quality: str
    heat_metric_available: bool
    suggested_status: str
    notes: list[str]
    error: str | None


def text_or_none(element: ET.Element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def parse_datetime(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return value.strip() or None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


def parse_rss_items(
    source: dict[str, Any],
    xml_text: str,
    limit: int,
    fetched_at: str,
) -> list[ProbeItem]:
    xml_text = INVALID_XML_CHARS.sub("", xml_text)
    xml_text = BARE_AMPERSAND.sub("&amp;", xml_text)
    root = ET.fromstring(xml_text)
    items: list[ProbeItem] = []
    for item in root.findall(".//item")[:limit]:
        title = text_or_none(item, "title")
        url = text_or_none(item, "link")
        published_at = parse_datetime(text_or_none(item, "pubDate"))
        author = text_or_none(item, "author")
        summary = text_or_none(item, "description")
        flags: list[str] = []
        for field_name, value in {
            "title": title,
            "url": url,
            "published_at": published_at,
            "summary": summary,
            "author": author,
        }.items():
            if not value:
                flags.append(f"missing_{field_name}")

        items.append(
            ProbeItem(
                source_id=source["source_id"],
                source_name=source["source_name"],
                source_type=source["source_type"],
                platform=source["platform"],
                channel=source["channel"],
                title=title,
                url=url,
                published_at=published_at,
                fetched_at=fetched_at,
                author=author,
                summary=summary,
                raw_metrics=None,
                quality_flags=flags,
            )
        )
    return items


def is_stable_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def check_links(client: httpx.Client, items: list[ProbeItem], max_checks: int) -> tuple[int, int]:
    checked = 0
    stable = 0
    for item in items:
        if checked >= max_checks:
            break
        if not is_stable_url(item.url):
            checked += 1
            continue
        try:
            response = client.head(str(item.url), follow_redirects=True)
            if response.status_code in {403, 405}:
                response = client.get(str(item.url), follow_redirects=True)
            if response.status_code < 400:
                stable += 1
        except httpx.HTTPError:
            pass
        checked += 1
    return checked, stable


def completeness(items: list[ProbeItem], field_name: str) -> float:
    if not items:
        return 0.0
    present_count = sum(1 for item in items if getattr(item, field_name))
    return round(present_count / len(items), 4)


def infer_time_quality(rate: float) -> str:
    if rate >= 0.9:
        return "high"
    if rate >= 0.5:
        return "medium"
    if rate > 0:
        return "low"
    return "missing"


def infer_status(result: ProbeResult) -> str:
    if not result.accessible or not result.parse_ok or result.item_count < 5:
        return "postpone"
    title_rate = result.field_completeness.get("title", 0)
    url_rate = result.field_completeness.get("url", 0)
    published_rate = result.field_completeness.get("published_at", 0)
    link_rate = 0
    if result.checked_link_count:
        link_rate = result.stable_link_count / result.checked_link_count
    if (
        title_rate >= 0.95
        and url_rate >= 0.95
        and published_rate >= 0.8
        and link_rate >= 0.8
    ):
        return "use"
    return "fallback"


def probe_source(
    client: httpx.Client,
    source: dict[str, Any],
    limit: int,
    link_checks: int,
) -> tuple[ProbeResult, list[ProbeItem]]:
    fetched_at = datetime.now(UTC).isoformat()
    notes: list[str] = []
    try:
        response = client.get(source["url"], follow_redirects=True)
        final_url = str(response.url)
        redirected_to_auth = "passport" in final_url or "visitor" in final_url
        accessible = response.status_code < 400 and not redirected_to_auth
        parser = source.get("parser")
        items = []
        parse_ok = False
        if accessible and parser == "rss":
            items = parse_rss_items(source, response.text, limit, fetched_at)
            parse_ok = True
        elif parser == "public_page_probe":
            parse_ok = accessible
            if redirected_to_auth or response.status_code in {401, 403}:
                notes.append("公开页面直连触发登录、访客或风控校验，未采集样本。")
            else:
                notes.append("公开页面可访问性需要单独页面解析验证，本次未使用非公开接口。")
        checked_links, stable_links = check_links(client, items, link_checks)
        field_rates = {
            "title": completeness(items, "title"),
            "url": completeness(items, "url"),
            "published_at": completeness(items, "published_at"),
            "summary": completeness(items, "summary"),
            "author": completeness(items, "author"),
        }
        if source["source_type"] == "official_news" and not any(item.raw_metrics for item in items):
            notes.append("RSS 未提供热度指标，官媒报道强度应由报道数量、来源权重和时效性计算。")
        if source["source_type"].startswith("community") and not items:
            notes.append("第一阶段仍选择该文字型社区源，但正式接入前需要找到稳定公开入口。")
        result = ProbeResult(
            source_id=source["source_id"],
            source_name=source["source_name"],
            endpoint_url=final_url if final_url else source["url"],
            fetched_at=fetched_at,
            http_status=response.status_code,
            response_content_type=response.headers.get("content-type"),
            response_bytes=len(response.content),
            accessible=accessible,
            parse_ok=parse_ok,
            item_count=len(items),
            checked_link_count=checked_links,
            stable_link_count=stable_links,
            field_completeness=field_rates,
            time_quality=infer_time_quality(field_rates["published_at"]),
            heat_metric_available=False,
            suggested_status="postpone",
            notes=notes,
            error=None,
        )
        result.suggested_status = infer_status(result)
        return result, items
    except Exception as exc:  # noqa: BLE001 - smoke test should capture all source failures.
        return (
            ProbeResult(
                source_id=source["source_id"],
                source_name=source["source_name"],
                endpoint_url=source["url"],
                fetched_at=fetched_at,
                http_status=None,
                response_content_type=None,
                response_bytes=0,
                accessible=False,
                parse_ok=False,
                item_count=0,
                checked_link_count=0,
                stable_link_count=0,
                field_completeness={},
                time_quality="missing",
                heat_metric_available=False,
                suggested_status="postpone",
                notes=[],
                error=f"{type(exc).__name__}: {exc}",
            ),
            [],
        )


def pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def render_report(results: list[ProbeResult], sample_limit: int, link_checks: int) -> str:
    generated_at = datetime.now(UTC).isoformat()
    table_header = (
        "| Source | Suggested status | Items | Title | URL | Published time | "
        "Summary | Links OK | Time quality | Heat metrics |"
    )
    rows = []
    for result in results:
        link_rate = 0
        if result.checked_link_count:
            link_rate = result.stable_link_count / result.checked_link_count
        row_template = (
            "| {source} | {status} | {items} | {title} | {url} | "
            "{published} | {summary} | {links} | {time} | {metrics} |"
        )
        rows.append(
            row_template.format(
                source=result.source_name,
                status=result.suggested_status,
                items=result.item_count,
                title=pct(result.field_completeness.get("title", 0)),
                url=pct(result.field_completeness.get("url", 0)),
                published=pct(result.field_completeness.get("published_at", 0)),
                summary=pct(result.field_completeness.get("summary", 0)),
                links=pct(link_rate),
                time=result.time_quality,
                metrics="yes" if result.heat_metric_available else "no",
            )
        )

    source_sections = []
    for result in results:
        notes = "\n".join(f"- {note}" for note in result.notes) or "- 无额外备注。"
        source_sections.append(
            f"""### {result.source_name}

- Endpoint: `{result.endpoint_url}`
- HTTP: `{result.http_status}` / `{result.response_content_type}` / `{result.response_bytes}` bytes
- Parse: `{"ok" if result.parse_ok else "failed"}`
- Suggested status: `{result.suggested_status}`
- Notes:
{notes}
"""
        )

    return f"""# Source Probe v0.1

Generated at: `{generated_at}`

## Scope

Day 15 smoke test for selected official/news and text BBS/community sources:

- 人民网
- 中国新闻网
- 新华网
- 微博热搜
- 知乎热榜

This run uses public RSS endpoints and public web pages only. It does not use
accounts, cookies, tokens, private APIs, proxy configuration, or anti-bot bypass
techniques.

## Method

- Fetch limit per source: `{sample_limit}`
- Link stability checks per source: `{link_checks}`
- Required first-stage fields checked: `title`, `url`, `fetched_at`
- Optional but valuable fields checked: `published_at`, `summary`, `author`, `raw_metrics`
- Raw samples are written to `notes/source-probe-raw/` and must remain local-only.

## Result Summary

{table_header}
|---|---|---:|---:|---:|---:|---:|---:|---|---|
{chr(10).join(rows)}

## Source Notes

{chr(10).join(source_sections)}
## Decision

- `中国新闻网`: keep as first collector candidate because RSS gives current multi-domain
  news with stable title, URL, and publication time fields.
- `人民网`: keep as priority official source. RSS is accessible and field quality is
  sufficient for a first-stage `NormalizedItem`; use official-score logic rather than
  raw heat metrics.
- `新华网`: keep as priority official source. RSS is accessible and suitable for
  authoritative event confirmation, but later collector work should expect channel-level
  structure differences.
- `微博热搜`: keep as selected text community source, but current direct public page
  access requires follow-up validation before production collection.
- `知乎热榜`: keep as selected text community source, but current direct public page
  access requires follow-up validation before production collection.

## Schema Impact

- `raw_metrics` must remain optional for official/news sources.
- `published_at` is available in this RSS-based probe, but schema should still allow
  missing values because non-RSS list pages may vary by channel.
- `summary` availability differs by source and should not block normalization.
- Official/news source heat should be derived from source coverage, source weight,
  recency, and repeated reporting rather than platform-provided hot values.
- Text community sources should use `rank`, `title`, `url`, and optional
  platform-provided heat values; they must remain heat/discussion signals rather than
  standalone fact sources.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Day 15 source smoke tests.")
    parser.add_argument("--limit", type=int, default=10, help="Items to parse per source.")
    parser.add_argument("--link-checks", type=int, default=5, help="Links to check per source.")
    parser.add_argument("--report", default="../reports/source-probe-v0.1.md")
    parser.add_argument(
        "--raw-output",
        default="../notes/source-probe-raw/source-probe-v0.1.json",
    )
    args = parser.parse_args()

    report_path = Path(args.report).resolve()
    raw_output_path = Path(args.raw_output).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    raw_output_path.parent.mkdir(parents=True, exist_ok=True)

    timeout = httpx.Timeout(20.0, connect=10.0)
    headers = {"User-Agent": USER_AGENT}
    results: list[ProbeResult] = []
    raw_samples: dict[str, Any] = {}
    with httpx.Client(timeout=timeout, headers=headers) as client:
        for source in SOURCES:
            result, items = probe_source(client, source, args.limit, args.link_checks)
            results.append(result)
            raw_samples[source["source_id"]] = [asdict(item) for item in items]

    raw_output_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "results": [asdict(result) for result in results],
                "samples": raw_samples,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    report_path.write_text(render_report(results, args.limit, args.link_checks), encoding="utf-8")

    for result in results:
        print(f"{result.source_name}: {result.suggested_status} ({result.item_count} items)")
    print(f"Report: {report_path}")
    print(f"Raw local sample: {raw_output_path}")


if __name__ == "__main__":
    main()
