"""Day 17 Zhihu hot_list -> zhihu_search sampling probe.

This is a low-frequency source validation script, not a production collector.
It writes raw samples to the local-only notes directory and a summarized report
to reports/.
"""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings
from app.services.zhihu_client import (
    ZhihuClient,
    ZhihuSearchItem,
    normalize_hot_list_items,
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
    "？",
    "?",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe Zhihu hot_list with zhihu_search enrichment."
    )
    parser.add_argument("--hot-list-limit", type=int, default=min(settings.zhihu_fetch_limit, 10))
    parser.add_argument("--search-top-n", type=int, default=5)
    parser.add_argument("--search-count", type=int, default=3)
    parser.add_argument(
        "--raw-output",
        default="../notes/source-probe-raw/zhihu-hotlist-search-sampling.json",
    )
    parser.add_argument(
        "--report",
        default="../reports/zhihu-hotlist-search-sampling-v0.1.md",
    )
    args = parser.parse_args()

    hot_list_limit = max(1, min(args.hot_list_limit, 30))
    search_top_n = max(1, min(args.search_top_n, hot_list_limit))
    search_count = max(1, min(args.search_count, 5))

    client = ZhihuClient()
    started_at = datetime.now(UTC)
    quota_before = client.fetch_quota()
    hot_list = client.fetch_hot_list(limit=hot_list_limit)
    hot_items = normalize_hot_list_items(hot_list)

    enriched_candidates: list[dict[str, Any]] = []
    retained_results: list[dict[str, Any]] = []
    rejected_results: list[dict[str, Any]] = []
    search_errors: list[dict[str, Any]] = []

    for rank, candidate in enumerate(hot_list.items[:search_top_n], start=1):
        title = candidate.title or ""
        query = build_search_query(title)
        candidate_record: dict[str, Any] = {
            "rank": rank,
            "title": title,
            "url": candidate.url,
            "query": query,
            "search_count": search_count,
            "retained_count": 0,
            "rejected_count": 0,
            "quality_flags": [],
            "search_items": [],
        }
        if not query:
            candidate_record["quality_flags"].append("empty_query")
            enriched_candidates.append(candidate_record)
            continue

        try:
            search_result = client.search(query=query, count=search_count)
        except Exception as exc:  # noqa: BLE001 - smoke script should record and continue.
            candidate_record["quality_flags"].append("search_failed")
            search_errors.append(
                {
                    "rank": rank,
                    "title": title,
                    "query": query,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            enriched_candidates.append(candidate_record)
            continue

        relations_by_identity: dict[str, dict[str, Any]] = {}
        for item in search_result.items:
            relation = assess_relatedness(
                candidate_title=title,
                candidate_url=candidate.url,
                query=query,
                item=item,
            )
            relations_by_identity[search_item_identity(item)] = relation

        normalized_items = normalize_search_items(
            search_result,
            candidate_title=title,
            candidate_url=candidate.url,
            candidate_rank=rank,
            relations_by_identity=relations_by_identity,
        )

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
            candidate_record["search_items"].append(item_record)
            if relation["is_highly_related"]:
                retained_results.append(normalized)
                candidate_record["retained_count"] += 1
            else:
                rejected_results.append(item_record)
                candidate_record["rejected_count"] += 1

        if candidate_record["retained_count"] == 0:
            candidate_record["quality_flags"].append("no_high_related_search_result")
        if candidate_record["rejected_count"] > 0:
            candidate_record["quality_flags"].append("search_noise_detected")
        enriched_candidates.append(candidate_record)

    quota_after = client.fetch_quota()
    raw_sample = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "parameters": {
            "hot_list_limit": hot_list_limit,
            "search_top_n": search_top_n,
            "search_count": search_count,
        },
        "quota_before": quota_before.raw_payload,
        "quota_after": quota_after.raw_payload,
        "hot_list": {
            "fetched_at": hot_list.fetched_at.isoformat(),
            "total": hot_list.total,
            "item_count": len(hot_list.items),
            "normalized_samples": hot_items,
            "raw_payload": hot_list.raw_payload,
        },
        "zhihu_search_enrichment": {
            "candidate_count": len(enriched_candidates),
            "retained_result_count": len(retained_results),
            "rejected_result_count": len(rejected_results),
            "search_error_count": len(search_errors),
            "candidates": enriched_candidates,
            "retained_normalized_samples": retained_results,
            "rejected_samples": rejected_results,
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

    print(f"Zhihu hot_list items: {len(hot_list.items)}")
    print(f"zhihu_search candidates: {len(enriched_candidates)}")
    print(f"related search results retained: {len(retained_results)}")
    print(f"low-related search results rejected: {len(rejected_results)}")
    print(f"Raw local sample: {raw_output}")
    print(f"Report: {report_output}")


def build_search_query(title: str) -> str:
    query = title.strip()
    for phrase in STOP_PHRASES:
        query = query.replace(phrase, "")
    query = re.sub(r"[，。！？?：:；;、（）()【】\[\]「」『』“”\"']", " ", query)
    query = re.sub(r"\s+", " ", query).strip()
    if len(query) <= 28:
        return query
    chunks = CHINESE_OR_WORD_RE.findall(query)
    if chunks:
        return "".join(chunks)[:28]
    return query[:28]


def assess_relatedness(
    *,
    candidate_title: str,
    candidate_url: str | None,
    query: str,
    item: ZhihuSearchItem,
) -> dict[str, Any]:
    candidate_qid = extract_question_id(candidate_url)
    item_qid = extract_question_id(item.url)
    haystack = normalize_text(
        " ".join(value or "" for value in (item.title, item.content_text, item.url))
    )
    normalized_title = normalize_text(candidate_title)
    normalized_query = normalize_text(query)
    keyword_overlap = calculate_keyword_overlap(
        candidate_title, " ".join((item.title or "", item.content_text or ""))
    )

    reasons: list[str] = []
    if candidate_qid and candidate_qid == item_qid:
        reasons.append("same_question_id")
    if normalized_title and normalized_title in haystack:
        reasons.append("candidate_title_contained")
    if normalized_query and normalized_query in haystack:
        reasons.append("query_contained")
    if keyword_overlap >= 0.55:
        reasons.append("keyword_overlap_high")

    return {
        "is_highly_related": bool(reasons),
        "reasons": reasons,
        "candidate_question_id": candidate_qid,
        "result_question_id": item_qid,
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


def render_report(*, raw_sample: dict[str, Any], raw_output: Path) -> str:
    generated_at = raw_sample["finished_at"]
    params = raw_sample["parameters"]
    hot = raw_sample["hot_list"]
    enrichment = raw_sample["zhihu_search_enrichment"]
    candidates = enrichment["candidates"]
    retained = enrichment["retained_result_count"]
    rejected = enrichment["rejected_result_count"]
    errors = enrichment["search_error_count"]

    hot_field_counter = Counter[str]()
    for item in hot["normalized_samples"]:
        for key, value in item["raw_payload"].items():
            if value is not None:
                hot_field_counter[key] += 1

    search_field_counter = Counter[str]()
    metric_counter = Counter[str]()
    relation_reasons = Counter[str]()
    for candidate in candidates:
        for item in candidate["search_items"]:
            for key in (
                "title",
                "url",
                "content_type",
                "content_id",
                "comment_count",
                "vote_up_count",
                "edit_time",
                "ranking_score",
                "authority_level",
            ):
                if item.get(key) is not None:
                    search_field_counter[key] += 1
            for key in ("comment_count", "vote_up_count", "edit_time", "ranking_score"):
                if item.get(key) is not None:
                    metric_counter[key] += 1
            relation_reasons.update(item["relation"]["reasons"])

    lines = [
        "# Zhihu Hotlist Search Sampling v0.1",
        "",
        f"Generated at: `{generated_at}`",
        "",
        "## Scope",
        "",
        "Day 17 smoke test for Zhihu official `hot_list` plus low-frequency `zhihu_search` enrichment.",
        "",
        "This run uses the Zhihu Data Open Platform JSON APIs with local credentials. Raw samples are local-only and are not suitable for public commit.",
        "",
        "## Method",
        "",
        f"- `hot_list` request limit: `{params['hot_list_limit']}`",
        f"- `zhihu_search` enrichment candidates: top `{params['search_top_n']}` hot list items",
        f"- `zhihu_search` count per candidate: `{params['search_count']}`",
        "- Search query strategy: remove generic question wording, punctuation, and trim to a short core phrase.",
        "- Relatedness filter: keep results with matching question id, strong title/query containment, or high keyword overlap.",
        f"- Raw local sample: `{raw_output}`",
        "",
        "## Result Summary",
        "",
        "| API | Calls | Items / results | Retained | Rejected | Main role |",
        "|---|---:|---:|---:|---:|---|",
        f"| `hot_list` | 1 | {hot['item_count']} | {hot['item_count']} | 0 | Mode A Zhihu hotspot candidate discovery |",
        f"| `zhihu_search` | {len(candidates)} | {retained + rejected} | {retained} | {rejected} | Low-frequency engagement/evidence enrichment for existing candidates |",
        "",
        "## hot_list Observed Data",
        "",
        f"- Returned item count: `{hot['item_count']}`",
        f"- Returned total: `{hot['total']}`",
        "- Stable observed item fields:",
    ]
    for key in ("Title", "Url", "ThumbnailUrl", "Summary"):
        lines.append(f"- `{key}` present in `{hot_field_counter[key]}/{hot['item_count']}` items")
    lines.extend(
        [
            "- Derived fields for project use: `rank` from array order, `fetched_at` from collector time.",
            "- Missing fields: no observed `hot_value`, no observed per-item `published_at`, no observed author/comment/vote metrics.",
            "",
            "## zhihu_search Observed Data",
            "",
            f"- Enriched candidates: `{len(candidates)}`",
            f"- Search errors: `{errors}`",
            f"- High-related retained results: `{retained}`",
            f"- Low-related rejected results: `{rejected}`",
            "- Stable observed fields in returned search items:",
        ]
    )
    search_total = retained + rejected
    for key in (
        "title",
        "content_type",
        "content_id",
        "url",
        "comment_count",
        "vote_up_count",
        "edit_time",
        "ranking_score",
        "authority_level",
    ):
        lines.append(f"- `{key}` present in `{search_field_counter[key]}/{search_total}` results")
    lines.extend(
        [
            "- Useful raw signal features:",
            f"- `CommentCount`: `{metric_counter['comment_count']}/{search_total}` results",
            f"- `VoteUpCount`: `{metric_counter['vote_up_count']}/{search_total}` results",
            f"- `RankingScore`: `{metric_counter['ranking_score']}/{search_total}` results",
            f"- `EditTime`: `{metric_counter['edit_time']}/{search_total}` results",
            "",
            "## Candidate-Level Enrichment",
            "",
            "| Rank | Query | Retained | Rejected | Quality flags |",
            "|---:|---|---:|---:|---|",
        ]
    )
    for candidate in candidates:
        flags = ", ".join(candidate["quality_flags"]) or "none"
        lines.append(
            f"| {candidate['rank']} | `{candidate['query']}` | {candidate['retained_count']} | {candidate['rejected_count']} | {flags} |"
        )
    lines.extend(
        [
            "",
            "## Relatedness Filter Notes",
            "",
            f"- Relation reasons observed: `{dict(relation_reasons)}`",
            "- Retained results can provide Zhihu-side engagement features for an existing `hot_list` event candidate.",
            "- Rejected results should stay in audit logs only and must not participate in event scoring.",
            "- If no retained search result exists for a candidate, the event should still keep the `hot_list` rank signal and skip search-derived engagement metrics.",
            "",
            "## Compared With Official Media RSS",
            "",
            "Compared against the Day 15 official-media RSS probe for 人民网, 中国新闻网, and 新华网.",
            "",
            "Zhihu adds:",
            "",
            "- Community-side hotspot discovery through `hot_list` rank.",
            "- Discussion/engagement metrics from `zhihu_search`: comments, upvotes, ranking score, edit time, content type, content id, and author metadata where present.",
            "- Question/community URLs that can act as durable platform ids for event matching.",
            "- Better signal for public attention and discussion focus, especially for platform, consumer, entertainment, and controversy topics.",
            "",
            "Zhihu lacks or is weaker on:",
            "",
            "- No observed `hot_list` publication time; RSS news sources usually provide `published_at`, except the current 新华网 fallback endpoint.",
            "- No observed `hot_list` platform heat value; rank must be used instead of a real heat metric.",
            "- Lower authority as factual evidence than official-media RSS; Zhihu should not be treated as the primary fact source.",
            "- Query-driven `zhihu_search` can introduce historical pollution and same-keyword noise, so it needs the relatedness gate.",
            "",
            "Official-media RSS adds:",
            "",
            "- Stronger factual citation and authority/evidence role.",
            "- More stable publication time and article-style summaries for 人民网 and 中国新闻网.",
            "- Better suitability for event confirmation, chronology, and source coverage metrics.",
            "",
            "Official-media RSS lacks:",
            "",
            "- No platform engagement metrics in the observed RSS samples.",
            "- No native rank or hot-list position.",
            "- Weaker coverage of community-first topics before official outlets report them.",
            "",
            "## Schema Impact",
            "",
            "- `hot_list` should map to a candidate-level `discussion_focus_signal` / `attention_signal` with `rank`, `total`, `fetched_at`, and nullable `published_at` / `hot_value`.",
            "- `zhihu_search` should map to a child/enrichment `SourceSignal` attached to an existing event candidate, not an independent Mode A discovery source.",
            "- `quality_flags` must include `no_high_related_search_result`, `search_noise_detected`, `missing_edit_time`, and missing metric flags.",
            "- Scoring should use retained `zhihu_search` metrics only after relatedness filtering; otherwise use only the `hot_list` rank signal.",
            "",
            "## Decision",
            "",
            "- Keep `hot_list` as `source_status = use` for Mode A Zhihu hotspot discovery.",
            "- Keep `zhihu_search` as low-frequency enrichment for Top K `hot_list` candidates and as a future Mode B evidence tool.",
            "- Do not convert `zhihu_search` metrics directly into a standalone final Zhihu heat score; keep them as raw engagement features for later normalization.",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
