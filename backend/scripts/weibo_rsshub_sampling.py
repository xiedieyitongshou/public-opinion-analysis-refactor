"""Low-frequency RSSHub Weibo hot search smoke test for Day 18.

This script validates RSSHub output shape only. It does not use Weibo account
cookies, proxy pools, login automation, or anti-crawler bypass logic.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin

import httpx

from app.core.config import settings

USER_AGENT = "public-opinion-analysis-weibo-rsshub-smoke-test/0.1 (+low-frequency)"
INVALID_XML_CHARS = re.compile(
    "[^\u0009\u000a\u000d\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)
BARE_AMPERSAND = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)")
HTML_TAG = re.compile(r"<[^>]+>")
HOT_VALUE = re.compile(r"(?<!\d)(\d{4,})(?!\d)")


@dataclass
class RSSHubRouteConfig:
    route_id: str
    label: str
    path: str
    expected_role: str


@dataclass
class WeiboRSSHubItem:
    rank: int
    title: str | None
    url: str | None
    description: str | None
    published_at: str | None
    guid: str | None
    hot_value: int | None
    quality_flags: list[str]


@dataclass
class WeiboRSSHubRouteResult:
    route_id: str
    label: str
    url: str
    fetched_at: str
    http_status: int | None
    response_content_type: str | None
    response_bytes: int
    accessible: bool
    parse_ok: bool
    item_count: int
    field_completeness: dict[str, float]
    hot_value_available: bool
    suggested_status: str
    notes: list[str]
    error: str | None
    items: list[WeiboRSSHubItem]


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/"


def build_url(base_url: str, route: str) -> str:
    return urljoin(normalize_base_url(base_url), route.lstrip("/"))


def clean_xml(text: str) -> str:
    text = INVALID_XML_CHARS.sub("", text)
    return BARE_AMPERSAND.sub("&amp;", text)


def element_text(element: ET.Element, tag: str) -> str | None:
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
    return parsed.astimezone(UTC).isoformat()


def plain_text(value: str | None) -> str | None:
    if not value:
        return None
    text = HTML_TAG.sub(" ", value)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def parse_hot_value(title: str | None, description: str | None) -> int | None:
    text = f"{title or ''} {plain_text(description) or ''}"
    matches = [int(match) for match in HOT_VALUE.findall(text)]
    return max(matches) if matches else None


def parse_items(xml_text: str, limit: int) -> list[WeiboRSSHubItem]:
    root = ET.fromstring(clean_xml(xml_text))
    parsed_items: list[WeiboRSSHubItem] = []
    for rank, item in enumerate(root.findall(".//item")[:limit], start=1):
        title = element_text(item, "title")
        url = element_text(item, "link")
        description = element_text(item, "description")
        published_at = parse_datetime(element_text(item, "pubDate"))
        guid = element_text(item, "guid")
        hot_value = parse_hot_value(title, description)
        flags: list[str] = []
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
                description=plain_text(description),
                published_at=published_at,
                guid=guid,
                hot_value=hot_value,
                quality_flags=flags,
            )
        )
    return parsed_items


def completeness(items: list[WeiboRSSHubItem], field_name: str) -> float:
    if not items:
        return 0.0
    present_count = sum(1 for item in items if getattr(item, field_name))
    return round(present_count / len(items), 4)


def infer_status(result: WeiboRSSHubRouteResult) -> str:
    if not result.accessible or not result.parse_ok or result.item_count < 5:
        return "postpone"
    if (
        result.field_completeness.get("title", 0) >= 0.95
        and result.field_completeness.get("url", 0) >= 0.95
    ):
        return "fallback"
    return "postpone"


def fetch_with_retries(
    client: httpx.Client,
    url: str,
    retries: int,
) -> tuple[httpx.Response | None, str | None, int]:
    attempts = max(1, retries + 1)
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(url, follow_redirects=True)
            return response, None, attempt
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    return None, last_error, attempts


def probe_route(
    client: httpx.Client,
    route_config: RSSHubRouteConfig,
    base_url: str,
    limit: int,
    retries: int,
) -> WeiboRSSHubRouteResult:
    fetched_at = datetime.now(UTC).isoformat()
    url = build_url(base_url, route_config.path)
    response, error, attempts = fetch_with_retries(client, url, retries)
    notes = [
        "不使用 WEIBO_COOKIES、微博账号 cookie、代理池或反爬绕过逻辑。",
        f"请求尝试次数：{attempts}。",
    ]
    if response is None:
        return WeiboRSSHubRouteResult(
            route_id=route_config.route_id,
            label=route_config.label,
            url=url,
            fetched_at=fetched_at,
            http_status=None,
            response_content_type=None,
            response_bytes=0,
            accessible=False,
            parse_ok=False,
            item_count=0,
            field_completeness={},
            hot_value_available=False,
            suggested_status="postpone",
            notes=notes,
            error=error,
            items=[],
        )

    accessible = response.status_code < 400
    items: list[WeiboRSSHubItem] = []
    parse_ok = False
    parse_error: str | None = None
    if accessible:
        try:
            items = parse_items(response.text, limit)
            parse_ok = True
        except Exception as exc:  # noqa: BLE001 - smoke test should capture shape failures.
            parse_error = f"{type(exc).__name__}: {exc}"
    else:
        notes.append("RSSHub route 返回非 2xx/3xx 状态，本次不解析 item。")

    field_rates = {
        "title": completeness(items, "title"),
        "url": completeness(items, "url"),
        "description": completeness(items, "description"),
        "published_at": completeness(items, "published_at"),
        "guid": completeness(items, "guid"),
        "hot_value": completeness(items, "hot_value"),
    }
    hot_value_available = any(item.hot_value is not None for item in items)
    if parse_ok and items:
        notes.append("可用 RSS item 顺序生成 rank。")
    if not hot_value_available:
        notes.append("未稳定观察到可解析 hot_value，后续应保持 optional。")
    if field_rates["published_at"] == 0:
        notes.append("未观察到 item 级 pubDate，后续由 fetched_at 记录采集时间。")
    if route_config.route_id == "hot_fulltext":
        notes.append("fulltext 路径会额外抓取热搜词下内容，耗时和失败概率高于基础路径。")
    if parse_error:
        notes.append("RSS XML 结构解析失败。")

    result = WeiboRSSHubRouteResult(
        route_id=route_config.route_id,
        label=route_config.label,
        url=str(response.url),
        fetched_at=fetched_at,
        http_status=response.status_code,
        response_content_type=response.headers.get("content-type"),
        response_bytes=len(response.content),
        accessible=accessible,
        parse_ok=parse_ok,
        item_count=len(items),
        field_completeness=field_rates,
        hot_value_available=hot_value_available,
        suggested_status="postpone",
        notes=notes,
        error=parse_error,
        items=items,
    )
    result.suggested_status = infer_status(result)
    return result


def pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def render_sample_table(items: list[WeiboRSSHubItem], count: int = 8) -> str:
    rows = [
        "| Rank | Title | URL | Description | pubDate | hot_value |",
        "|---:|---|---|---|---|---:|",
    ]
    for item in items[:count]:
        title = item.title or ""
        url = item.url or ""
        description = item.description or ""
        if len(description) > 80:
            description = description[:77] + "..."
        row_template = (
            "| {rank} | `{title}` | {url} | `{description}` | "
            "{published_at} | {hot_value} |"
        )
        rows.append(
            row_template.format(
                rank=item.rank,
                title=title.replace("|", "\\|"),
                url=f"[link]({url})" if url else "",
                description=description.replace("|", "\\|"),
                published_at=f"`{item.published_at}`" if item.published_at else "",
                hot_value=item.hot_value if item.hot_value is not None else "",
            )
        )
    return "\n".join(rows)


def render_report(
    results: list[WeiboRSSHubRouteResult],
    *,
    base_url: str,
    limit: int,
    retries: int,
    failure_threshold: int,
    raw_output_path: Path,
) -> str:
    generated_at = datetime.now(UTC).isoformat()
    rows = []
    for result in results:
        row_template = (
            "| {api} | {status} | {items} | {title} | {url} | "
            "{desc} | {pub} | {guid} | {hot} |"
        )
        rows.append(
            row_template.format(
                api=result.label,
                status=result.suggested_status,
                items=result.item_count,
                title=pct(result.field_completeness.get("title", 0)),
                url=pct(result.field_completeness.get("url", 0)),
                desc=pct(result.field_completeness.get("description", 0)),
                pub=pct(result.field_completeness.get("published_at", 0)),
                guid=pct(result.field_completeness.get("guid", 0)),
                hot=pct(result.field_completeness.get("hot_value", 0)),
            )
        )

    route_sections = []
    for result in results:
        notes = "\n".join(f"- {note}" for note in result.notes) or "- 无额外备注。"
        route_sections.append(
            f"""### {result.label}

- Endpoint: `{result.url}`
- HTTP: `{result.http_status}` / `{result.response_content_type}` / `{result.response_bytes}` bytes
- Parse: `{"ok" if result.parse_ok else "failed"}`
- Suggested status: `{result.suggested_status}`
- Observed items: `{result.item_count}`
- Notes:
{notes}

{render_sample_table(result.items)}
"""
        )

    return f"""# 微博 RSSHub 热搜采样 v0.1

生成时间：`{generated_at}`

## 范围

Day 18 针对 RSSHub 微博热搜路径做 smoke test 和字段结构审计。

本次验证两个 RSSHub 路径：

- 基础热搜列表：`/weibo/search/hot`
- fulltext 摘要增强：`/weibo/search/hot/fulltext`

本次运行只通过 RSSHub 获取 RSS XML，不使用微博账号 cookie，不配置代理池，
不做登录自动化或反爬绕过。原始样本仅本地保留，不适合提交到公开仓库。

## 方法

- RSSHub base URL：`{base_url}`
- 每个路径抓取上限：`{limit}`
- HTTP timeout：`{settings.weibo_rsshub_timeout_seconds}` 秒
- 最大重试：`{retries}`
- 连续失败降级阈值草案：`{failure_threshold}`
- 原始本地样本：`{raw_output_path}`
- 字段审计：`title`、`link`、`description`、`pubDate`、`guid`、item 顺序、可解析 `hot_value`

## 结果汇总

| API / Route | Suggested status | Items | Title | URL | Description | pubDate | guid | hot_value |
|---|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

## 两个 API 分别获得了什么数据

{chr(10).join(route_sections)}
## 与官媒 RSS 对比

微博 RSSHub 多了：

- 提供社区即时注意力入口，能直接补充官媒未报道或尚未充分报道的公众关注话题。
- 提供榜单顺序，可派生 `rank`，用于微博侧 `attention_signal` 和后续 `velocity` 计算。
- 链接指向微博搜索页，适合做事件候选发现和后续人工核验入口。
- 话题标题更短、更像搜索词，适合捕捉平台型争议、娱乐、消费、游戏和突发社会话题。

微博 RSSHub 少了或更弱：

- 没有稳定观察到 item 级 `pubDate`，不能直接提供事件发生时间或发布时间。
- 没有稳定观察到 `hot_value`，第一阶段不能把微博热度值作为必需评分输入。
- `description` 在基础路径中通常等于标题；fulltext 路径虽然可能增强摘要，但稳定性和成本更差。
- RSSHub 属第三方转换层，`source_status` 应保持 `fallback`，
  不能像人民网 / 中国新闻网 RSS 那样作为强依赖。
- 微博热搜本身不是事实来源，只能作为关注信号；事实确认仍需官媒、新闻源或其它可引用证据补强。

官媒 RSS 多了：

- 人民网和中国新闻网在 Day 15 样本中提供稳定 `title`、`url`、
  `published_at` 和 `description`，更适合事实证据、时间线和来源引用。
- 官媒 RSS 的报道文本更完整，适合做事件确认、摘要生成和 `news_evidence_score`。
- 来源权威性更高，可作为 `authority_score` / `coverage_score` 的输入。

官媒 RSS 少了：

- 没有榜单顺序或社区热度指标，不能直接表示公众注意力。
- 对社区先发话题响应可能滞后，容易漏掉短周期平台争议。
- 不覆盖或低覆盖娱乐、游戏、亚文化和平台社区内部争议。

## Schema 影响

- 微博 RSSHub 应映射为 `source_origin = rsshub`、
  `source_status = fallback`、`signal_role = attention_signal`。
- `rank` 从 item 顺序生成，写入 `raw_metrics.rank`。
- `hot_value` 和 `published_at` 必须允许为空；缺失时分别写入
  `missing_hot_value`、`missing_pub_date`。
- fulltext 路径不应作为默认定时采集路径；基础路径足够支撑热榜候选发现，
  fulltext 只适合作为低频验证或人工辅助。
- 评分阶段只能先做平台内归一化，再进入事件级总分；不能把微博 rank、
  知乎互动数和官媒报道数量直接相加。

## 决策

- `weibo_direct_hot_search = postpone`
- `weibo_rsshub_hot_search = fallback`
- Day 33 可实现微博 RSSHub fallback collector，但默认只使用 `/weibo/search/hot`。
- RSSHub 请求失败、403、503 或结构异常时跳过微博源，不阻塞新闻源和知乎源。
- RSSHub endpoint 保持环境变量化，允许本地、自建服务器或公共实例切换；公共实例不可作为生产强依赖。
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Day 18 Weibo RSSHub smoke test.")
    parser.add_argument("--base-url", default=settings.weibo_rsshub_base_url)
    parser.add_argument("--route", default=settings.weibo_rsshub_route)
    parser.add_argument("--fulltext-route", default=settings.weibo_rsshub_fulltext_route)
    parser.add_argument("--limit", type=int, default=settings.weibo_rsshub_fetch_limit)
    parser.add_argument("--retries", type=int, default=settings.weibo_rsshub_max_retries)
    parser.add_argument("--report", default="../reports/weibo-rsshub-sampling-v0.1.md")
    parser.add_argument(
        "--raw-output",
        default="../notes/source-probe-raw/weibo-rsshub-sampling.json",
    )
    args = parser.parse_args()

    limit = max(1, min(args.limit, 50))
    report_path = Path(args.report).resolve()
    raw_output_path = Path(args.raw_output).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    raw_output_path.parent.mkdir(parents=True, exist_ok=True)

    route_configs = [
        RSSHubRouteConfig(
            route_id="hot_basic",
            label="基础热搜列表 `/weibo/search/hot`",
            path=args.route,
            expected_role="hotlist_discovery",
        ),
        RSSHubRouteConfig(
            route_id="hot_fulltext",
            label="fulltext 摘要增强 `/weibo/search/hot/fulltext`",
            path=args.fulltext_route,
            expected_role="low_frequency_enrichment_probe",
        ),
    ]

    timeout = httpx.Timeout(settings.weibo_rsshub_timeout_seconds, connect=10.0)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"}
    with httpx.Client(timeout=timeout, headers=headers) as client:
        results = [
            probe_route(client, route_config, args.base_url, limit, args.retries)
            for route_config in route_configs
        ]

    raw_output_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "base_url": args.base_url,
                "limit": limit,
                "routes": [
                    {
                        **asdict(result),
                        "items": [asdict(item) for item in result.items],
                    }
                    for result in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    report_path.write_text(
        render_report(
            results,
            base_url=args.base_url,
            limit=limit,
            retries=args.retries,
            failure_threshold=settings.weibo_rsshub_failure_threshold,
            raw_output_path=raw_output_path,
        ),
        encoding="utf-8",
    )

    for result in results:
        print(f"{result.label}: {result.suggested_status} ({result.item_count} items)")
    print(f"Report: {report_path}")
    print(f"Raw local sample: {raw_output_path}")


if __name__ == "__main__":
    main()
