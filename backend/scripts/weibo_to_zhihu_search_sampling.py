"""Day 18 Weibo RSSHub hot topics -> Zhihu search sampling probe.

This is a low-frequency validation script, not a production collector. It uses
RSSHub for Weibo topic discovery, skips the first hot-list item, then uses
Zhihu's official zhihu_search API to test whether those topics have measurable
discussion signals on Zhihu.
"""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from weibo_rsshub_sampling import (
    USER_AGENT,
    build_url,
    fetch_with_retries,
    parse_items,
)

from app.core.config import settings
from app.services.zhihu_client import (
    ZhihuClient,
    ZhihuSearchItem,
    normalize_search_items,
)

QUESTION_ID_RE = re.compile(r"/question/(\d+)")
CHINESE_OR_WORD_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]+")
STOP_PHRASES = (
    "如何看待",
    "怎么看待",
    "如何评价",
    "为什么",
    "为何",
    "有哪些",
    "是什么",
    "真的",
    "吗",
    "微博",
    "热搜",
    "#",
    "？",
    "?",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe Weibo RSSHub hot topics with Zhihu search enrichment."
    )
    parser.add_argument("--base-url", default=settings.weibo_rsshub_base_url)
    parser.add_argument("--route", default=settings.weibo_rsshub_route)
    parser.add_argument("--weibo-limit", type=int, default=20)
    parser.add_argument("--skip-top", type=int, default=1)
    parser.add_argument("--search-topic-count", type=int, default=10)
    parser.add_argument("--zhihu-count", type=int, default=3)
    parser.add_argument("--retries", type=int, default=settings.weibo_rsshub_max_retries)
    parser.add_argument(
        "--raw-output",
        default="../notes/source-probe-raw/weibo-to-zhihu-search-sampling.json",
    )
    parser.add_argument(
        "--report",
        default="../reports/weibo-to-zhihu-search-sampling-v0.1.md",
    )
    args = parser.parse_args()

    weibo_limit = max(2, min(args.weibo_limit, 50))
    skip_top = max(0, min(args.skip_top, weibo_limit - 1))
    search_topic_count = max(1, min(args.search_topic_count, weibo_limit - skip_top))
    zhihu_count = max(1, min(args.zhihu_count, 5))

    started_at = datetime.now(UTC)
    weibo_url = build_url(args.base_url, args.route)
    weibo_result = fetch_weibo_topics(
        weibo_url=weibo_url,
        limit=weibo_limit,
        retries=args.retries,
    )
    candidate_topics = weibo_result["items"][skip_top : skip_top + search_topic_count]

    zhihu_client = ZhihuClient()
    quota_before = try_fetch_quota(zhihu_client)
    topic_results: list[dict[str, Any]] = []
    retained_normalized_samples: list[dict[str, Any]] = []
    rejected_samples: list[dict[str, Any]] = []
    search_errors: list[dict[str, Any]] = []

    for topic in candidate_topics:
        query = build_search_query(topic["title"] or "")
        topic_record: dict[str, Any] = {
            "weibo_rank": topic["rank"],
            "weibo_title": topic["title"],
            "weibo_url": topic["url"],
            "query": query,
            "zhihu_requested_count": zhihu_count,
            "zhihu_returned_count": 0,
            "retained_count": 0,
            "rejected_count": 0,
            "quality_flags": [],
            "zhihu_items": [],
        }
        if not query:
            topic_record["quality_flags"].append("empty_query")
            topic_results.append(topic_record)
            continue

        try:
            search_result = zhihu_client.search(query=query, count=zhihu_count)
        except Exception as exc:  # noqa: BLE001 - smoke script should record and continue.
            topic_record["quality_flags"].append("zhihu_search_failed")
            search_errors.append(
                {
                    "weibo_rank": topic["rank"],
                    "weibo_title": topic["title"],
                    "query": query,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            topic_results.append(topic_record)
            continue

        relations_by_identity: dict[str, dict[str, Any]] = {}
        for item in search_result.items:
            relation = assess_relatedness(
                topic_title=topic["title"] or "",
                query=query,
                item=item,
            )
            relations_by_identity[search_item_identity(item)] = relation

        normalized_items = normalize_search_items(
            search_result,
            candidate_title=topic["title"] or "",
            candidate_url=topic["url"],
            candidate_rank=topic["rank"],
            relations_by_identity=relations_by_identity,
        )
        topic_record["zhihu_returned_count"] = len(search_result.items)

        for item, normalized in zip(search_result.items, normalized_items, strict=True):
            relation = relations_by_identity[search_item_identity(item)]
            item_record = {
                "title": item.title,
                "url": item.url,
                "content_type": item.content_type,
                "content_id": item.content_id,
                "comment_count": item.comment_count,
                "vote_up_count": item.vote_up_count,
                "edit_time": item.edit_time,
                "ranking_score": item.ranking_score,
                "authority_level": item.authority_level,
                "relation": relation,
            }
            topic_record["zhihu_items"].append(item_record)
            if relation["is_highly_related"]:
                topic_record["retained_count"] += 1
                retained_normalized_samples.append(normalized)
            else:
                topic_record["rejected_count"] += 1
                rejected_samples.append(item_record)

        if topic_record["retained_count"] == 0:
            topic_record["quality_flags"].append("no_zhihu_discussion_found")
        if topic_record["rejected_count"] > 0:
            topic_record["quality_flags"].append("zhihu_search_noise_detected")
        topic_results.append(topic_record)

    quota_after = try_fetch_quota(zhihu_client)
    raw_sample = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "parameters": {
            "weibo_rsshub_base_url": args.base_url,
            "weibo_rsshub_route": args.route,
            "weibo_limit": weibo_limit,
            "skip_top": skip_top,
            "search_topic_count": search_topic_count,
            "zhihu_count": zhihu_count,
            "rsshub_retries": args.retries,
        },
        "quota_before": quota_before,
        "quota_after": quota_after,
        "weibo_rsshub": weibo_result,
        "weibo_to_zhihu_search": {
            "candidate_count": len(candidate_topics),
            "searched_topic_count": len(topic_results),
            "retained_result_count": len(retained_normalized_samples),
            "rejected_result_count": len(rejected_samples),
            "search_error_count": len(search_errors),
            "topics": topic_results,
            "retained_normalized_samples": retained_normalized_samples,
            "rejected_samples": rejected_samples,
            "errors": search_errors,
        },
    }

    raw_output = Path(args.raw_output).resolve()
    raw_output.parent.mkdir(parents=True, exist_ok=True)
    raw_output.write_text(json.dumps(raw_sample, ensure_ascii=False, indent=2), encoding="utf-8")

    report_output = Path(args.report).resolve()
    report_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.write_text(
        render_report(raw_sample=raw_sample, raw_output=raw_output),
        encoding="utf-8",
    )

    summary = raw_sample["weibo_to_zhihu_search"]
    print(f"Weibo RSSHub items: {weibo_result['item_count']}")
    print(f"Skipped Weibo top items: {skip_top}")
    print(f"Weibo topics searched on Zhihu: {summary['searched_topic_count']}")
    print(f"Related Zhihu results retained: {summary['retained_result_count']}")
    print(f"Low-related Zhihu results rejected: {summary['rejected_result_count']}")
    print(f"Zhihu search errors: {summary['search_error_count']}")
    print(f"Raw local sample: {raw_output}")
    print(f"Report: {report_output}")


def fetch_weibo_topics(*, weibo_url: str, limit: int, retries: int) -> dict[str, Any]:
    timeout = httpx.Timeout(settings.weibo_rsshub_timeout_seconds, connect=10.0)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"}
    fetched_at = datetime.now(UTC).isoformat()
    with httpx.Client(timeout=timeout, headers=headers) as client:
        response, error, attempts = fetch_with_retries(client, weibo_url, retries)

    if response is None:
        return {
            "url": weibo_url,
            "fetched_at": fetched_at,
            "http_status": None,
            "response_content_type": None,
            "response_bytes": 0,
            "parse_ok": False,
            "item_count": 0,
            "items": [],
            "error": error,
            "attempts": attempts,
        }

    items = []
    parse_ok = False
    parse_error = None
    if response.status_code < 400:
        try:
            items = [asdict(item) for item in parse_items(response.text, limit)]
            parse_ok = True
        except Exception as exc:  # noqa: BLE001 - smoke script should record shape failures.
            parse_error = f"{type(exc).__name__}: {exc}"

    return {
        "url": str(response.url),
        "fetched_at": fetched_at,
        "http_status": response.status_code,
        "response_content_type": response.headers.get("content-type"),
        "response_bytes": len(response.content),
        "parse_ok": parse_ok,
        "item_count": len(items),
        "items": items,
        "error": parse_error,
        "attempts": attempts,
    }


def try_fetch_quota(client: ZhihuClient) -> dict[str, Any] | None:
    try:
        return client.fetch_quota().raw_payload
    except Exception:  # noqa: BLE001 - quota is diagnostic and must not block sampling.
        return None


def build_search_query(title: str) -> str:
    query = title.strip()
    for phrase in STOP_PHRASES:
        query = query.replace(phrase, "")
    query = re.sub(r"[，。！：:；;、（）()【】\[\]「」『』“”\"']", " ", query)
    query = re.sub(r"\s+", " ", query).strip()
    if len(query) <= 28:
        return query
    chunks = CHINESE_OR_WORD_RE.findall(query)
    if chunks:
        return "".join(chunks)[:28]
    return query[:28]


def assess_relatedness(
    *,
    topic_title: str,
    query: str,
    item: ZhihuSearchItem,
) -> dict[str, Any]:
    haystack = normalize_text(
        " ".join(value or "" for value in (item.title, item.content_text, item.url))
    )
    normalized_title = normalize_text(topic_title)
    normalized_query = normalize_text(query)
    keyword_overlap = calculate_keyword_overlap(
        topic_title, " ".join((item.title or "", item.content_text or ""))
    )

    reasons: list[str] = []
    if normalized_title and normalized_title in haystack:
        reasons.append("topic_title_contained")
    if normalized_query and normalized_query in haystack:
        reasons.append("query_contained")
    if keyword_overlap >= 0.55:
        reasons.append("keyword_overlap_high")
    return {
        "is_highly_related": bool(reasons),
        "reasons": reasons,
        "result_question_id": extract_question_id(item.url),
        "keyword_overlap": round(keyword_overlap, 3),
    }


def extract_question_id(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    match = QUESTION_ID_RE.search(parsed.path)
    return match.group(1) if match else None


def search_item_identity(item: ZhihuSearchItem) -> str:
    if item.url:
        return item.url
    if item.content_id is not None:
        return str(item.content_id)
    return item.title or ""


def normalize_text(value: str) -> str:
    return "".join(CHINESE_OR_WORD_RE.findall(value)).lower()


def calculate_keyword_overlap(left: str, right: str) -> float:
    left_tokens = keyword_tokens(left)
    right_tokens = keyword_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    shared = left_tokens & right_tokens
    return len(shared) / len(left_tokens)


def keyword_tokens(value: str) -> set[str]:
    text = normalize_text(value)
    tokens: set[str] = set()
    words = CHINESE_OR_WORD_RE.findall(value.lower())
    tokens.update(word for word in words if len(word) >= 2)
    max_ngram = min(6, len(text))
    for size in range(2, max_ngram + 1):
        tokens.update(text[index : index + size] for index in range(0, len(text) - size + 1))
    return tokens


def present_count(items: list[dict[str, Any]], key: str) -> int:
    return sum(1 for item in items if item.get(key) is not None)


def pct(count: int, total: int) -> str:
    if total <= 0:
        return "0%"
    return f"{count / total * 100:.0f}%"


def render_topic_table(topics: list[dict[str, Any]]) -> str:
    rows = [
        "| Weibo rank | Weibo topic | Query | Zhihu returned | Retained | Rejected | Quality flags |",
        "|---:|---|---|---:|---:|---:|---|",
    ]
    for topic in topics:
        flags = ", ".join(topic["quality_flags"]) or "none"
        rows.append(
            "| {rank} | `{title}` | `{query}` | {returned} | {retained} | {rejected} | {flags} |".format(
                rank=topic["weibo_rank"],
                title=(topic["weibo_title"] or "").replace("|", "\\|"),
                query=topic["query"].replace("|", "\\|"),
                returned=topic["zhihu_returned_count"],
                retained=topic["retained_count"],
                rejected=topic["rejected_count"],
                flags=flags,
            )
        )
    return "\n".join(rows)


def render_retained_metric_table(topics: list[dict[str, Any]], max_rows: int = 12) -> str:
    rows = [
        "| Weibo rank | Weibo topic | Zhihu title | Comments | Upvotes | Ranking score | Relation reasons |",
        "|---:|---|---|---:|---:|---:|---|",
    ]
    count = 0
    for topic in topics:
        for item in topic["zhihu_items"]:
            if not item["relation"]["is_highly_related"]:
                continue
            rows.append(
                "| {rank} | `{topic}` | `{title}` | {comments} | {upvotes} | {score} | {reasons} |".format(
                    rank=topic["weibo_rank"],
                    topic=(topic["weibo_title"] or "").replace("|", "\\|"),
                    title=(item["title"] or "").replace("|", "\\|"),
                    comments=item["comment_count"] if item["comment_count"] is not None else "",
                    upvotes=item["vote_up_count"] if item["vote_up_count"] is not None else "",
                    score=item["ranking_score"] if item["ranking_score"] is not None else "",
                    reasons=", ".join(item["relation"]["reasons"]),
                )
            )
            count += 1
            if count >= max_rows:
                return "\n".join(rows)
    return "\n".join(rows)


def render_report(*, raw_sample: dict[str, Any], raw_output: Path) -> str:
    generated_at = raw_sample["finished_at"]
    params = raw_sample["parameters"]
    weibo = raw_sample["weibo_rsshub"]
    combined = raw_sample["weibo_to_zhihu_search"]
    topics = combined["topics"]
    retained = combined["retained_result_count"]
    rejected = combined["rejected_result_count"]
    total_zhihu_items = retained + rejected
    topics_with_discussion = sum(1 for topic in topics if topic["retained_count"] > 0)
    topic_count = len(topics)

    weibo_items = weibo["items"]
    weibo_field_counts = {
        "title": present_count(weibo_items, "title"),
        "url": present_count(weibo_items, "url"),
        "description": present_count(weibo_items, "description"),
        "published_at": present_count(weibo_items, "published_at"),
        "hot_value": present_count(weibo_items, "hot_value"),
    }

    zhihu_items = [item for topic in topics for item in topic["zhihu_items"]]
    zhihu_field_counts = {
        "title": present_count(zhihu_items, "title"),
        "url": present_count(zhihu_items, "url"),
        "content_type": present_count(zhihu_items, "content_type"),
        "content_id": present_count(zhihu_items, "content_id"),
        "comment_count": present_count(zhihu_items, "comment_count"),
        "vote_up_count": present_count(zhihu_items, "vote_up_count"),
        "edit_time": present_count(zhihu_items, "edit_time"),
        "ranking_score": present_count(zhihu_items, "ranking_score"),
    }
    relation_reasons = Counter[str]()
    for item in zhihu_items:
        relation_reasons.update(item["relation"]["reasons"])

    return f"""# 微博热榜到知乎搜索增强采样 v0.1

生成时间：`{generated_at}`

## 范围

Day 18 组合路径测试：先用 RSSHub `/weibo/search/hot` 获取微博热榜话题，
再用微博话题标题调用知乎官方 `zhihu_search` API，验证这些微博热榜话题在知乎是否存在可用讨论信号。

本次实验只是 source feasibility test，不等于正式接入决策。是否在后续 collector 中使用，
取决于微博 RSSHub 稳定性、知乎搜索命中率、相关性过滤后的保留率和互动字段完整率。

## 方法

- RSSHub base URL：`{params['weibo_rsshub_base_url']}`
- RSSHub route：`{params['weibo_rsshub_route']}`
- 微博抓取上限：`{params['weibo_limit']}`
- 跳过微博热榜前 `{params['skip_top']}` 条：第 1 条通常可能是置顶 / 宣传位，不作为自然热度样本。
- 进入知乎搜索的话题数：`{params['search_topic_count']}`，本次为微博 rank 2 起的连续话题。
- 每个微博话题的 `zhihu_search` 返回上限：`{params['zhihu_count']}`
- 原始本地样本：`{raw_output}`
- 相关性过滤：保留标题 / 内容强包含、query 强包含或关键词重合度高的结果。

## 结果汇总

| Stage | Count / rate | 说明 |
|---|---:|---|
| RSSHub 微博热榜返回 item | {weibo['item_count']} | 基础热搜列表可解析 |
| 跳过微博热榜 item | {params['skip_top']} | 跳过 rank 1 |
| 调用知乎搜索的话题 | {topic_count} | 使用微博 title 作为 query |
| 知乎返回结果 | {total_zhihu_items} | 所有 `zhihu_search` 返回条目 |
| 高相关保留结果 | {retained} | 可作为知乎讨论补充信号 |
| 低相关拒绝结果 | {rejected} | 只进入审计，不参与评分 |
| 有知乎讨论命中的微博话题 | {topics_with_discussion}/{topic_count} | 至少 1 条高相关知乎结果 |
| 知乎搜索错误 | {combined['search_error_count']} | API 或网络失败 |

## 微博 RSSHub 获得的数据

| 字段 | 完整率 | 是否可用于热度 |
|---|---:|---|
| `title` | {pct(weibo_field_counts['title'], weibo['item_count'])} | 可用于话题发现和知乎 query |
| `url` / `link` | {pct(weibo_field_counts['url'], weibo['item_count'])} | 可用于来源引用 / 人工核验 |
| `description` | {pct(weibo_field_counts['description'], weibo['item_count'])} | 本次通常等于标题，信息增量弱 |
| `rank` | 100% | 由 RSS item 顺序派生，可做弱 attention signal |
| `published_at` / `pubDate` | {pct(weibo_field_counts['published_at'], weibo['item_count'])} | 本次不可用 |
| `hot_value` | {pct(weibo_field_counts['hot_value'], weibo['item_count'])} | 本次不可用 |

## 知乎搜索补充获得的数据

| 字段 | 完整率 | 是否可用于热度 |
|---|---:|---|
| `title` | {pct(zhihu_field_counts['title'], total_zhihu_items)} | 用于相关性判断 |
| `url` | {pct(zhihu_field_counts['url'], total_zhihu_items)} | 可用于来源引用 / 去重 |
| `content_type` | {pct(zhihu_field_counts['content_type'], total_zhihu_items)} | 可用于区分问题 / 回答 / 内容类型 |
| `content_id` | {pct(zhihu_field_counts['content_id'], total_zhihu_items)} | 可用于平台内去重 |
| `comment_count` | {pct(zhihu_field_counts['comment_count'], total_zhihu_items)} | 可作为讨论强度 raw feature |
| `vote_up_count` | {pct(zhihu_field_counts['vote_up_count'], total_zhihu_items)} | 可作为互动强度 raw feature |
| `edit_time` | {pct(zhihu_field_counts['edit_time'], total_zhihu_items)} | 可作为 freshness raw feature |
| `ranking_score` | {pct(zhihu_field_counts['ranking_score'], total_zhihu_items)} | 可作为知乎搜索排序 raw feature |

## 话题级命中结果

{render_topic_table(topics)}

## 高相关知乎结果样例

{render_retained_metric_table(topics)}

## 相关性过滤观察

- 相关性原因统计：`{dict(relation_reasons)}`
- 高相关样例表只展示知乎标题；部分命中可能来自 `ContentText`，后续 collector
  需要保存 relation reasons 以便审计。
- 只有高相关保留结果可以参与后续事件评分。
- 低相关结果只保留在审计日志中，避免微博短标题误命中知乎历史内容。
- 未命中知乎讨论的话题仍可保留微博 `rank` / `topic_present`，但必须标记 `no_zhihu_discussion_found`。

## 与单独微博 RSSHub 方案对比

单独微博 RSSHub 多数只能提供话题标题、链接和派生 rank，不能提供阅读量、发帖数、点赞、
评论、转发或稳定 `hot_value`。组合路径可以用知乎 `zhihu_search` 补到部分讨论和互动指标，
但这些指标代表“知乎上的相关讨论”，不是微博自身热度。

## 与官媒 RSS 对比

组合路径多了：

- 微博热榜提供社区即时话题发现，能覆盖官媒未报道或尚未报道的话题。
- 知乎搜索补充提供 `comment_count`、`vote_up_count`、`ranking_score`、`edit_time` 等可量化讨论特征。
- 微博 rank 与知乎互动字段结合后，可以比单独 RSSHub 更适合做社区热度试算。

组合路径少了或更弱：

- 微博侧仍没有阅读量、发帖数、点赞、评论、转发和稳定 `hot_value`。
- 知乎搜索命中的是跨平台相关讨论，不等于微博话题本身的热度。
- 微博短标题可能导致知乎搜索噪音，必须依赖相关性过滤。
- 官媒 RSS 更适合事实确认、发布时间、事件时间线和权威引用；组合路径不能替代事实来源。

## Schema 影响

- 微博 RSSHub 条目映射为 `attention_signal`，保留 `weibo_rank`、`topic_present`、`fetched_at`。
- 知乎搜索结果作为微博话题候选下的 `discussion_signal` / enrichment signal。
- 事件评分中可使用知乎 `comment_count`、`vote_up_count`、`ranking_score` 和 `edit_time`，但必须标明来源是知乎讨论补充。
- 未命中知乎讨论时，不补造讨论热度；只保留微博弱话题发现信号并降低置信度。
- 微博 rank、知乎互动数、官媒报道数量仍然不能直接相加，必须先按来源归一化。

## 决策建议

- 本组合路径可以继续进入 Day 19/20 的跨源事件样本和归一化试算。
- Day 33 如实现 collector，微博 RSSHub 仍保持 `fallback`。
- 默认跳过微博热榜 rank 1。
- 默认配置建议：微博 rank 2-11、每个微博话题 `zhihu_search` 返回 3 条。
- 是否正式使用该组合路径，应根据多轮采样后的知乎命中率、相关性拒绝率、RSSHub 成功率和额度消耗决定。
"""


if __name__ == "__main__":
    main()
