"""Bounded, extractive context shared by matching and briefing display."""

import re
from html import unescape

from app.services.match_documents import value
from app.services.weibo_relevance import clean_body


def plain(raw):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", raw or ""))).strip()


def topic_excerpts(item):
    """Only accepted CLI bodies; preserve provenance and never promote rejected samples."""
    raw = value(item, "raw_payload_json", "raw_payload", default={}) or {}
    title = value(item, "title", default="") or ""
    enrichment = raw.get("cli_enrichment") or {}
    excerpts, seen = [], set()
    for sample in enrichment.get("samples", []):
        relation = sample.get("relation") or {}
        if relation.get("decision") != "accepted" or relation.get("audit_only"):
            continue
        body = plain(clean_body(sample.get("text") or "", title))[:360]
        identity = re.sub(r"\W", "", body).lower()
        if len(identity) < 12 or identity in seen or not sample.get("url"):
            continue
        seen.add(identity)
        excerpts.append(
            {"text": body, "url": sample["url"], "published_at": sample.get("created_at")}
        )
        if len(excerpts) == 3:
            break
    return excerpts


def topic_context(item):
    excerpts = topic_excerpts(item)
    if excerpts:
        return "\n".join(row["text"] for row in excerpts)
    # This is source description, never an invented event narrative.
    title = plain(value(item, "title", default=""))
    description = plain(value(item, "summary", default=""))
    return description[:900] if description != title else ""


def summary_from_items(items):
    for item in items:
        if item.source.platform == "zhihu" and item.source.source_type in {
            "community_question_hotlist",
            "community_hotlist",
        }:
            description = topic_context(item)
            text = (
                ("知乎问题描述：" + description[:500])
                if description
                else ("知乎问题：" + item.title)
            )
            return text, [{"url": item.url, "text": description or item.title}]
    for item in items:
        excerpts = topic_excerpts(item)
        if excerpts:
            selected = [{**row, "text": row["text"][:200]} for row in excerpts[:2]]
            return "微博讨论摘录：" + "；".join(row["text"] for row in selected), selected
    for item in items:
        if item.source.source_type == "official_news" and topic_context(item):
            text = topic_context(item)[:500]
            return "报道摘录：" + text, [{"url": item.url, "text": text}]
    return "当前仅有话题标题，尚无足够正文可供摘要。", []
