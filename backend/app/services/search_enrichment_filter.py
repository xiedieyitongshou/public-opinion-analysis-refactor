"""Deterministic relevance filters for search-enrichment results."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.schemas import NormalizedItem, SearchEnrichmentRelation, SearchEnrichmentSource

NOISE_FLAGS = {
    "historical_content_pollution",
    "time_window_conflict",
    "marketing_spam",
    "unrelated_result",
    "entity_conflict",
}

TEXT_SPLIT = re.compile(r"[\s\W_]+", re.UNICODE)
HASHTAG = re.compile(r"#([^#\s]{2,80})#")
ZHihu_QUESTION = re.compile(r"/question/(\d+)")


def evaluate_search_enrichment(
    parent: NormalizedItem | dict[str, Any],
    enrichment: NormalizedItem | dict[str, Any],
    *,
    source: SearchEnrichmentSource,
    parent_candidate_id: str | None = None,
    parent_signal_id: str | None = None,
    user_query_id: str | None = None,
    min_keyword_overlap: float = 0.45,
) -> SearchEnrichmentRelation:
    """Classify whether a search result supports an existing event candidate.

    The filter is intentionally conservative: only high-related search results
    are accepted for classification support. Weak or noisy results remain
    auditable but do not affect event classification.
    """

    parent_item = _as_item(parent)
    enrichment_item = _as_item(enrichment)
    parent_title = parent_item.title or ""
    enrichment_title = enrichment_item.title or ""
    enrichment_text = _combined_text(enrichment_item)
    parent_text = _combined_text(parent_item)

    matched_by: list[str] = []
    rejected_by: list[str] = []
    quality_flags = list(enrichment_item.quality_flags)

    noisy_flags = sorted(set(quality_flags) & NOISE_FLAGS)
    if noisy_flags:
        return SearchEnrichmentRelation(
            source=source,
            parent_candidate_id=parent_candidate_id,
            parent_signal_id=parent_signal_id,
            user_query_id=user_query_id,
            parent_title=parent_title,
            enrichment_title=enrichment_title,
            decision="rejected",
            confidence=0.0,
            rejected_by=noisy_flags,
            quality_flags=_dedupe([*quality_flags, "search_enrichment_rejected"]),
            audit_only=True,
        )

    if _same_zhihu_question(parent_item.url, enrichment_item.url):
        matched_by.append("same_zhihu_question_id")

    if _strong_containment(parent_title, enrichment_text) or _strong_containment(
        enrichment_title, parent_text
    ):
        matched_by.append("title_containment")

    if source == "weibo_cli" and _hashtag_match(parent_title, enrichment_text):
        matched_by.append("hashtag_match")

    keyword_overlap = _keyword_overlap(parent_title, enrichment_text)
    if keyword_overlap >= min_keyword_overlap:
        matched_by.append("keyword_overlap_high")
    elif keyword_overlap >= 0.25:
        matched_by.append("keyword_overlap_medium")
    else:
        rejected_by.append("keyword_overlap_low")

    hard_match = any(
        reason in matched_by
        for reason in ("same_zhihu_question_id", "title_containment", "hashtag_match")
    )
    accepted = hard_match or keyword_overlap >= min_keyword_overlap
    confidence = _confidence(matched_by, keyword_overlap)

    if accepted:
        return SearchEnrichmentRelation(
            source=source,
            parent_candidate_id=parent_candidate_id,
            parent_signal_id=parent_signal_id,
            user_query_id=user_query_id,
            parent_title=parent_title,
            enrichment_title=enrichment_title,
            decision="accepted",
            confidence=confidence,
            matched_by=matched_by,
            rejected_by=[],
            quality_flags=quality_flags,
            audit_only=False,
        )

    return SearchEnrichmentRelation(
        source=source,
        parent_candidate_id=parent_candidate_id,
        parent_signal_id=parent_signal_id,
        user_query_id=user_query_id,
        parent_title=parent_title,
        enrichment_title=enrichment_title,
        decision="audit_only",
        confidence=confidence,
        matched_by=matched_by,
        rejected_by=rejected_by,
        quality_flags=_dedupe([*quality_flags, "low_relevance_search_result"]),
        audit_only=True,
    )


def _as_item(value: NormalizedItem | dict[str, Any]) -> NormalizedItem:
    if isinstance(value, NormalizedItem):
        return value
    return NormalizedItem.model_validate(value)


def _combined_text(item: NormalizedItem) -> str:
    parts = [
        item.title,
        item.summary,
        item.content,
        item.content_text,
        str(item.normalized.get("query", "")),
    ]
    return " ".join(part for part in parts if part)


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", value).lower()


def _strong_containment(needle: str | None, haystack: str | None) -> bool:
    normalized_needle = _normalize_text(needle)
    normalized_haystack = _normalize_text(haystack)
    return len(normalized_needle) >= 4 and normalized_needle in normalized_haystack


def _same_zhihu_question(left_url: str | None, right_url: str | None) -> bool:
    left_id = _zhihu_question_id(left_url)
    right_id = _zhihu_question_id(right_url)
    return left_id is not None and left_id == right_id


def _zhihu_question_id(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    match = ZHihu_QUESTION.search(parsed.path)
    if match:
        return match.group(1)
    query = parse_qs(parsed.query)
    values = query.get("question_id") or query.get("question")
    return values[0] if values else None


def _hashtag_match(parent_title: str | None, text: str | None) -> bool:
    normalized_parent = _normalize_text(parent_title)
    if len(normalized_parent) < 4 or not text:
        return False
    return any(normalized_parent == _normalize_text(tag) for tag in HASHTAG.findall(text))


def _keyword_overlap(parent_title: str | None, enrichment_text: str | None) -> float:
    parent_terms = _term_set(parent_title)
    enrichment_terms = _term_set(enrichment_text)
    if not parent_terms or not enrichment_terms:
        return 0.0
    return len(parent_terms & enrichment_terms) / len(parent_terms)


def _term_set(value: str | None) -> set[str]:
    normalized = _normalize_text(value)
    if not normalized:
        return set()
    chunks = [chunk for chunk in TEXT_SPLIT.split(normalized) if len(chunk) >= 2]
    terms = set(chunks)
    for size in (2, 3, 4):
        if len(normalized) >= size:
            terms.update(
                normalized[index : index + size]
                for index in range(len(normalized) - size + 1)
            )
    return terms


def _confidence(matched_by: list[str], keyword_overlap: float) -> float:
    if "same_zhihu_question_id" in matched_by:
        return 0.95
    if "title_containment" in matched_by:
        return 0.88
    if "hashtag_match" in matched_by:
        return 0.82
    if "keyword_overlap_high" in matched_by:
        return min(0.8, 0.45 + keyword_overlap)
    if "keyword_overlap_medium" in matched_by:
        return min(0.55, 0.2 + keyword_overlap)
    return 0.1


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
