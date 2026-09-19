"""Rule-first official media support matching for Day 43."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.schemas import (
    OfficialReference,
    OfficialSupportConfig,
    OfficialSupportDetail,
    OfficialSupportResult,
)

OFFICIAL_SIGNAL_ROLES = {"evidence_signal", "event_signal", "mixed_signal"}
OFFICIAL_SOURCE_STATUSES = {"use", "fallback"}
OFFICIAL_SOURCE_TYPE = "official_news"
ACTION_TERMS = {
    "发布",
    "公布",
    "通报",
    "回应",
    "启动",
    "开展",
    "推进",
    "实施",
    "调查",
    "处理",
    "整治",
    "处罚",
    "辟谣",
    "预警",
    "上线",
    "下架",
    "召回",
    "调整",
}


@dataclass(frozen=True)
class OfficialEvidenceItem:
    item_id: str | None
    title: str | None
    url: str | None
    source_name: str
    source_type: str | None
    source_status: str | None
    signal_role: str | None
    published_at: datetime | None
    fetched_at: datetime | None
    summary: str | None
    content: str | None
    event_text_for_match: str | None
    entities: list[str]
    action_terms: list[str]
    keywords: list[str]
    source_citation: dict[str, Any]
    raw_metrics: dict[str, Any]
    quality_flags: list[str]

    @property
    def match_text(self) -> str:
        parts = [
            self.title,
            self.summary,
            self.content,
            self.event_text_for_match,
            " ".join(self.entities),
            " ".join(self.action_terms),
            " ".join(self.keywords),
        ]
        return " ".join(part for part in parts if part)


@dataclass(frozen=True)
class EventForOfficialSupport:
    event_id: str | None
    title: str
    summary: str | None
    primary_entity: str | None
    entities: list[str]
    action_terms: list[str]
    keywords: list[str]
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    source_citations: list[dict[str, Any]]
    event_detail: dict[str, Any]

    @property
    def match_text(self) -> str:
        parts = [
            self.title,
            self.summary,
            self.primary_entity,
            " ".join(self.entities),
            " ".join(self.action_terms),
            " ".join(self.keywords),
        ]
        return " ".join(part for part in parts if part)


@dataclass(frozen=True)
class MatchCandidate:
    item: OfficialEvidenceItem
    status: str
    score: float
    reason: str
    features: dict[str, Any]
    quality_flags: list[str]


def match_official_support(
    event: Any,
    official_items: Iterable[Any] | None,
    config: OfficialSupportConfig | None = None,
) -> OfficialSupportResult:
    """Match one event against official media evidence already present in items."""

    config = config or OfficialSupportConfig()
    event_data = _event_data(event)
    if official_items is None:
        return _not_checked_result(event_data, "official_pool_not_provided")

    evidence_items = [_evidence_item(item) for item in official_items]
    evidence_items = [
        item
        for item in evidence_items
        if item.source_type == OFFICIAL_SOURCE_TYPE
        and item.source_status in OFFICIAL_SOURCE_STATUSES
        and item.signal_role in OFFICIAL_SIGNAL_ROLES
    ]

    if not evidence_items:
        citation_result = _result_from_existing_official_citations(event_data)
        if citation_result is not None:
            return citation_result
        return _not_checked_result(event_data, "official_pool_empty")

    candidates = [
        candidate
        for item in evidence_items[: config.recall_limit]
        if (candidate := _match_candidate(event_data, item, config)) is not None
    ]
    candidates.sort(key=lambda candidate: candidate.score, reverse=True)

    if not candidates:
        return OfficialSupportResult(
            event_id=event_data.event_id,
            official_support_status="not_found",
            official_support_detail=OfficialSupportDetail(
                official_source_count=len(evidence_items),
                authority_sources=sorted({item.source_name for item in evidence_items}),
                match_quality="none",
                quality_flags=["official_support_not_found"],
            ),
            quality_flags=["official_support_not_found"],
        )

    selected = candidates[: config.recall_limit]
    supported = any(candidate.status == "supported" for candidate in selected)
    status = "supported" if supported else "weak_supported"
    freshness_bucket = _best_freshness_bucket(candidate.features for candidate in selected)
    quality_flags = sorted({flag for candidate in selected for flag in candidate.quality_flags})
    if status == "weak_supported" and "official_support_weak" not in quality_flags:
        quality_flags.append("official_support_weak")

    return OfficialSupportResult(
        event_id=event_data.event_id,
        official_support_status=status,
        official_references=[_reference(candidate) for candidate in selected],
        official_support_detail=OfficialSupportDetail(
            official_source_count=len({candidate.item.source_name for candidate in selected}),
            authority_sources=sorted({candidate.item.source_name for candidate in selected}),
            freshness_bucket=freshness_bucket,
            match_quality="strong" if status == "supported" else "weak",
            matched_item_ids=[
                candidate.item.item_id
                for candidate in selected
                if candidate.item.item_id is not None
            ],
            quality_flags=quality_flags,
        ),
        quality_flags=quality_flags,
    )


def apply_official_support_to_event(event: Any, result: OfficialSupportResult) -> Any:
    """Persist MVP official support fields into an Event-like object's JSON columns."""

    detail = dict(getattr(event, "event_detail_json", None) or {})
    classification_detail = dict(detail.get("classification_detail") or {})
    classification_detail["official_support_detail"] = result.official_support_detail.model_dump(
        mode="json"
    )
    detail["official_support_status"] = result.official_support_status
    detail["classification_detail"] = classification_detail
    event.event_detail_json = detail

    existing_citations = list(getattr(event, "source_citations_json", None) or [])
    by_key = {
        _citation_key(citation): dict(citation)
        for citation in existing_citations
        if isinstance(citation, dict)
    }
    for reference in result.official_references:
        citation = reference.model_dump(mode="json")
        citation["citation_role"] = "official_support"
        by_key[_citation_key(citation)] = citation
    event.source_citations_json = list(by_key.values())
    return event


def _match_candidate(
    event: EventForOfficialSupport,
    item: OfficialEvidenceItem,
    config: OfficialSupportConfig,
) -> MatchCandidate | None:
    event_text = _normalize_text(event.match_text)
    item_text = _normalize_text(item.match_text)
    event_title = _normalize_text(event.title)
    item_title = _normalize_text(item.title or "")
    quality_flags = list(item.quality_flags)

    entity_overlap = _entity_overlap(event, item, item_text)
    if entity_overlap == 0.0 and (event.entities or event.primary_entity):
        return None

    action_overlap = _overlap_ratio(event.action_terms, item.action_terms, item_text)
    title_containment = _title_containment(event_title, item_title)
    keyword_overlap = _keyword_overlap(event.keywords, item_text, event_text, item_text)
    ngram_overlap = _ngram_overlap(event_text, item_text)
    time_distance_days = _time_distance_days(event, item)
    within_time_window = (
        time_distance_days is not None and time_distance_days <= config.time_window_days
    )
    has_url = bool(item.url)

    if not item.title:
        quality_flags.append("missing_title")
    if not has_url:
        quality_flags.append("missing_url")
    if item.published_at is None:
        quality_flags.append("missing_published_at")
    if item.source_status == "fallback":
        quality_flags.append("fallback_source")
    if time_distance_days is None:
        quality_flags.append("unknown_time_distance")
    elif not within_time_window:
        quality_flags.append("outside_time_window")

    strong_text_match = (
        title_containment
        or keyword_overlap >= config.keyword_overlap_supported
        or ngram_overlap >= config.ngram_overlap_supported
        or action_overlap >= 0.50
    )
    weak_text_match = (
        strong_text_match
        or keyword_overlap >= config.keyword_overlap_weak
        or ngram_overlap >= config.ngram_overlap_weak
        or action_overlap > 0
    )
    if not weak_text_match:
        return None

    features = {
        "entity_overlap": entity_overlap,
        "action_overlap": action_overlap,
        "title_containment": title_containment,
        "keyword_overlap": keyword_overlap,
        "ngram_overlap": ngram_overlap,
        "time_distance_days": time_distance_days,
        "within_time_window": within_time_window,
        "source_status": item.source_status,
        "has_url": has_url,
    }
    strong_source = item.source_status == "use"
    complete_fields = has_url and item.title and item.published_at is not None
    if strong_source and complete_fields and within_time_window and strong_text_match:
        return MatchCandidate(
            item=item,
            status="supported",
            score=1.0 + keyword_overlap + ngram_overlap + entity_overlap,
            reason=_match_reason(features, strong=True),
            features=features,
            quality_flags=sorted(set(quality_flags)),
        )

    return MatchCandidate(
        item=item,
        status="weak_supported",
        score=0.5 + keyword_overlap + ngram_overlap + entity_overlap,
        reason=_match_reason(features, strong=False),
        features=features,
        quality_flags=sorted(set(quality_flags + ["official_support_weak"])),
    )


def _event_data(event: Any) -> EventForOfficialSupport:
    detail = _dict_value(event, "event_detail_json", "event_detail", default={}) or {}
    source_citations = _dict_value(event, "source_citations_json", "source_citations", default=[])
    primary_entity = _string_value(event, "primary_entity")
    entities = _list_value(detail, "entities", "entity_terms")
    if primary_entity:
        entities.insert(0, primary_entity)
    return EventForOfficialSupport(
        event_id=_string_value(event, "event_id") or _string_value(event, "id"),
        title=_string_value(event, "title") or "",
        summary=_string_value(event, "summary"),
        primary_entity=primary_entity,
        entities=_dedupe([entity for entity in entities if entity]),
        action_terms=_dedupe(_list_value(detail, "action_terms", "actions")),
        keywords=_dedupe(
            _list_value(event, "keywords_json", "keywords")
            + _list_value(detail, "keywords", "topic_terms")
        ),
        first_seen_at=_datetime_value(event, "first_seen_at"),
        last_seen_at=_datetime_value(event, "last_seen_at"),
        source_citations=[
            citation for citation in source_citations if isinstance(citation, dict)
        ],
        event_detail=detail if isinstance(detail, dict) else {},
    )


def _evidence_item(item: Any) -> OfficialEvidenceItem:
    normalized = _dict_value(item, "normalized_json", "normalized", default={}) or {}
    source = getattr(item, "source", None)
    source_citation = _dict_value(item, "source_citation_json", "source_citation", default={}) or {}
    raw_metrics = _dict_value(item, "raw_metrics_json", "raw_metrics", default={}) or {}
    quality_flags = _list_value(item, "quality_flags_json", "quality_flags")
    item_id = _string_value(item, "item_id") or _string_value(item, "id")
    return OfficialEvidenceItem(
        item_id=item_id,
        title=_string_value(item, "title"),
        url=_string_value(item, "url"),
        source_name=_string_value(item, "source_name")
        or _string_value(source, "name")
        or str(normalized.get("source_name") or "unknown"),
        source_type=_string_value(item, "source_type")
        or _string_value(source, "source_type")
        or _string_value(normalized, "source_type"),
        source_status=_string_value(item, "source_status")
        or _string_value(source, "source_status")
        or _string_value(normalized, "source_status"),
        signal_role=_string_value(item, "signal_role") or _string_value(normalized, "signal_role"),
        published_at=_datetime_value(item, "published_at"),
        fetched_at=_datetime_value(item, "fetched_at"),
        summary=_string_value(item, "summary"),
        content=_string_value(item, "content") or _string_value(item, "content_text"),
        event_text_for_match=_string_value(item, "event_text_for_match")
        or _string_value(normalized, "event_text_for_match"),
        entities=_dedupe(_list_value(normalized, "entities", "entity_terms")),
        action_terms=_dedupe(_list_value(normalized, "action_terms", "actions")),
        keywords=_dedupe(_list_value(normalized, "keywords", "topic_terms")),
        source_citation=source_citation if isinstance(source_citation, dict) else {},
        raw_metrics=raw_metrics if isinstance(raw_metrics, dict) else {},
        quality_flags=quality_flags,
    )


def _reference(candidate: MatchCandidate) -> OfficialReference:
    item = candidate.item
    raw_metric: dict[str, Any] = {}
    if "read_count" in item.raw_metrics:
        raw_metric["read_count"] = item.raw_metrics["read_count"]
    return OfficialReference(
        item_id=item.item_id,
        title=item.title or "",
        url=item.url,
        source_name=item.source_name,
        source_status=item.source_status or "unknown",
        published_at=item.published_at.isoformat() if item.published_at else None,
        match_reason=candidate.reason,
        match_features=candidate.features,
        raw_metric=raw_metric,
        quality_flags=candidate.quality_flags,
    )


def _not_checked_result(event: EventForOfficialSupport, flag: str) -> OfficialSupportResult:
    return OfficialSupportResult(
        event_id=event.event_id,
        official_support_status="not_checked",
        official_support_detail=OfficialSupportDetail(
            match_quality="unknown",
            quality_flags=[flag],
        ),
        quality_flags=[flag],
    )


def _result_from_existing_official_citations(
    event: EventForOfficialSupport,
) -> OfficialSupportResult | None:
    official_citations = [
        citation
        for citation in event.source_citations
        if citation.get("source_type") == OFFICIAL_SOURCE_TYPE
        or citation.get("citation_role") == "official_support"
    ]
    if not official_citations:
        return None
    references = [
        OfficialReference(
            item_id=str(citation.get("item_id")) if citation.get("item_id") is not None else None,
            title=str(citation.get("title") or ""),
            url=citation.get("url"),
            source_name=str(citation.get("source_name") or "unknown"),
            source_status=str(citation.get("source_status") or "fallback"),
            published_at=(
                str(citation.get("published_at")) if citation.get("published_at") else None
            ),
            match_reason="existing_official_citation_snapshot",
            match_features={"citation_snapshot_only": True},
            quality_flags=["citation_snapshot_only", "official_support_weak"],
        )
        for citation in official_citations
    ]
    return OfficialSupportResult(
        event_id=event.event_id,
        official_support_status="weak_supported",
        official_references=references,
        official_support_detail=OfficialSupportDetail(
            official_source_count=len({reference.source_name for reference in references}),
            authority_sources=sorted({reference.source_name for reference in references}),
            freshness_bucket="unknown",
            match_quality="weak",
            matched_item_ids=[
                reference.item_id for reference in references if reference.item_id is not None
            ],
            quality_flags=["citation_snapshot_only", "official_support_weak"],
        ),
        quality_flags=["citation_snapshot_only", "official_support_weak"],
    )


def _entity_overlap(
    event: EventForOfficialSupport,
    item: OfficialEvidenceItem,
    item_text: str,
) -> float:
    event_entities = _dedupe(event.entities)
    if not event_entities:
        return 1.0
    matched = [entity for entity in event_entities if _normalize_text(entity) in item_text]
    if item.entities:
        item_entities = {_normalize_text(entity) for entity in item.entities}
        matched.extend(
            entity for entity in event_entities if _normalize_text(entity) in item_entities
        )
    return len(set(matched)) / len(event_entities)


def _overlap_ratio(terms: list[str], candidate_terms: list[str], candidate_text: str) -> float:
    if not terms:
        terms = [term for term in ACTION_TERMS if term in candidate_text]
    if not terms:
        return 0.0
    candidate_set = {_normalize_text(term) for term in candidate_terms}
    matched = [
        term
        for term in terms
        if _normalize_text(term) in candidate_set or _normalize_text(term) in candidate_text
    ]
    return len(set(matched)) / len(set(terms))


def _keyword_overlap(
    keywords: list[str],
    item_text: str,
    event_text: str,
    candidate_text: str,
) -> float:
    terms = _dedupe([keyword for keyword in keywords if keyword])
    if not terms:
        terms = _tokens(event_text)
    if not terms:
        return 0.0
    matched = [
        term for term in terms if _normalize_text(term) in item_text or term in candidate_text
    ]
    return len(set(matched)) / len(set(terms))


def _ngram_overlap(left: str, right: str) -> float:
    left_grams = _char_ngrams(left, n=2) | _char_ngrams(left, n=3)
    right_grams = _char_ngrams(right, n=2) | _char_ngrams(right, n=3)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams)


def _title_containment(event_title: str, item_title: str) -> bool:
    if len(event_title) < 6 or len(item_title) < 6:
        return False
    return event_title in item_title or item_title in event_title


def _time_distance_days(
    event: EventForOfficialSupport,
    item: OfficialEvidenceItem,
) -> float | None:
    event_time = event.last_seen_at or event.first_seen_at
    if event_time is None or item.published_at is None:
        return None
    event_time = _ensure_aware_utc(event_time)
    item_time = _ensure_aware_utc(item.published_at)
    return abs((event_time - item_time).total_seconds()) / 86400


def _best_freshness_bucket(features_list: Iterable[dict[str, Any]]) -> str:
    rank = {"24h": 0, "72h": 1, "7d": 2, "stale": 3, "unknown": 4}
    best = "unknown"
    for features in features_list:
        distance = features.get("time_distance_days")
        if distance is None:
            bucket = "unknown"
        elif distance <= 1:
            bucket = "24h"
        elif distance <= 3:
            bucket = "72h"
        elif distance <= 7:
            bucket = "7d"
        else:
            bucket = "stale"
        if rank[bucket] < rank[best]:
            best = bucket
    return best


def _match_reason(features: dict[str, Any], *, strong: bool) -> str:
    reasons: list[str] = []
    if features["title_containment"]:
        reasons.append("title_containment")
    if features["keyword_overlap"] > 0:
        reasons.append("keyword_overlap")
    if features["ngram_overlap"] > 0:
        reasons.append("ngram_overlap")
    if features["action_overlap"] > 0:
        reasons.append("action_overlap")
    if features["within_time_window"]:
        reasons.append("within_time_window")
    else:
        reasons.append("outside_or_unknown_time_window")
    prefix = "strong_official_match" if strong else "weak_official_match"
    return f"{prefix}: {','.join(reasons)}"


def _dict_value(item: Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if isinstance(item, dict) and key in item:
            return item[key]
        if not isinstance(item, dict) and hasattr(item, key):
            return getattr(item, key)
    return default


def _string_value(item: Any, *keys: str) -> str | None:
    value = _dict_value(item, *keys)
    if value is None:
        return None
    return str(value)


def _list_value(item: Any, *keys: str) -> list[str]:
    value = _dict_value(item, *keys, default=[])
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if isinstance(value, tuple | set):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _datetime_value(item: Any, *keys: str) -> datetime | None:
    value = _dict_value(item, *keys)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", "", (value or "").lower())


def _tokens(value: str) -> list[str]:
    latin = re.findall(r"[a-z0-9]{2,}", value.lower())
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", value)
    terms: list[str] = latin
    for chunk in chinese:
        terms.extend(_char_ngrams(chunk, n=2))
    return _dedupe(terms)


def _char_ngrams(value: str, *, n: int) -> set[str]:
    clean = _normalize_text(value)
    if len(clean) < n:
        return set()
    return {clean[index : index + n] for index in range(len(clean) - n + 1)}


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_text(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
    return result


def _citation_key(citation: dict[str, Any]) -> str:
    return str(citation.get("url") or citation.get("item_id") or citation.get("title") or "")
