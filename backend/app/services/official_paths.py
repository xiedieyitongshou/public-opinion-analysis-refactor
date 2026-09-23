"""Official media path orchestration for Day 45 B/C."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from app.agents.event_extractor import EventSignalExtractor
from app.agents.event_resolver import EventResolverAgent
from app.schemas import (
    EventMatchConfig,
    ExtractEventSignalsInput,
    MatchAndResolveEventsInput,
    OfficialAgendaRank,
    OfficialPathResult,
    OfficialQuery,
    OfficialSupportDetail,
    OfficialSupportResult,
    RetrievedEventMatchCandidate,
    SourceSignal,
    SourceSignalMatchRef,
)
from app.services.official_support import match_official_support


def build_official_query(event: Any) -> OfficialQuery:
    """Generate the MVP query for community-event official support enrichment."""

    event_id = _string_value(event, "event_id") or _string_value(event, "id")
    detail = _dict_value(event, "event_detail_json", "event_detail", default={}) or {}
    keywords = _list_value(event, "keywords_json", "keywords") + _list_value(
        detail,
        "keywords",
        "topic_terms",
    )
    primary_entity = _string_value(event, "primary_entity")
    title = _string_value(event, "title") or ""
    query_parts = [primary_entity, *keywords[:3]]
    query_text = " ".join(part for part in query_parts if part).strip() or title
    return OfficialQuery(
        query_text=query_text,
        event_id=event_id,
        generated_from="event_entity_keywords" if query_text != title else "event_title",
    )


def run_community_support_path(
    event: Any,
    official_items: Iterable[Any] | None,
    *,
    official_query: OfficialQuery | None = None,
) -> OfficialPathResult:
    """Path A: enrich an existing community event with official support evidence."""

    query = official_query or build_official_query(event)
    if not _has_community_presence(event):
        return OfficialPathResult(
            path_type="community_support",
            status="skipped",
            official_query=query,
            eligible_for_community_main_list=False,
            quality_flags=["community_presence_required"],
        )

    support_result = match_official_support(event, official_items)
    support_result.official_support_detail = enrich_support_detail(
        support_result,
        official_query=query,
    )
    status = "succeeded"
    fallback_flags = {
        "official_pool_empty",
        "official_query_unavailable",
        "official_query_failed",
        "official_support_not_found",
    }
    fallback_used = bool(fallback_flags.intersection(support_result.quality_flags))
    if support_result.official_support_status in {"not_checked", "not_found"}:
        status = "partial"

    return OfficialPathResult(
        path_type="community_support",
        status=status,
        official_query=query,
        official_support_result=support_result,
        eligible_for_community_main_list=True,
        fallback_used=fallback_used,
        output_references=[
            reference.model_dump(mode="json") for reference in support_result.official_references
        ],
        downstream_fields={
            "official_support_status": support_result.official_support_status,
            "official_support_detail": support_result.official_support_detail.model_dump(
                mode="json"
            ),
        },
        quality_flags=support_result.quality_flags,
    )


def normalized_item_to_source_signal(
    item: Any,
    *,
    path_type: str = "official_agenda",
    official_query: OfficialQuery | None = None,
    force_signal_role: str | None = None,
) -> SourceSignal:
    """Adapt an official NormalizedItem/Item-like object to SourceSignal."""

    normalized = _dict_value(item, "normalized_json", "normalized", default={}) or {}
    source_citation = _dict_value(item, "source_citation_json", "source_citation", default={}) or {}
    raw_metrics = _dict_value(item, "raw_metrics_json", "raw_metrics", default={}) or {}
    source = getattr(item, "source", None)
    item_id = _stable_item_id(item, source_citation)
    source_id = _string_value(item, "source_id") or _string_value(source, "name") or "official"
    title = _string_value(item, "title") or ""
    quality_flags = _list_value(item, "quality_flags_json", "quality_flags")
    if not item_id:
        item_id = _generated_item_id(source_id, title, _string_value(item, "url"))
        quality_flags.append("generated_official_item_id")
    signal_role = force_signal_role or (
        "event_signal" if path_type == "official_agenda" else "evidence_signal"
    )

    return SourceSignal(
        source_signal_id=_source_signal_id(path_type, item_id, title),
        item_id=item_id,
        source_id=source_id,
        source_type=_string_value(item, "source_type")
        or _string_value(source, "source_type")
        or "official_news",
        source_status=_string_value(item, "source_status")
        or _string_value(source, "source_status")
        or "use",
        source_origin=_string_value(item, "source_origin")
        or _string_value(source, "source_origin")
        or "official_rss",
        platform=_string_value(item, "platform")
        or _string_value(source, "platform")
        or _string_value(normalized, "platform")
        or "chinanews",
        signal_role=signal_role,
        title=title,
        url=_string_value(item, "url") or _string_value(source_citation, "url"),
        published_at=_datetime_string(item, "published_at"),
        fetched_at=_datetime_string(item, "fetched_at") or datetime.utcnow().isoformat(),
        raw_metrics=raw_metrics if isinstance(raw_metrics, dict) else {},
        platform_features={
            "source_name": _string_value(item, "source_name")
            or _string_value(source, "name")
            or _string_value(source_citation, "source_name"),
            "channel": _string_value(item, "channel") or _string_value(normalized, "channel"),
        },
        classification_features={
            "official_path_type": path_type,
            "official_query": official_query.model_dump(mode="json") if official_query else None,
            "source_citation": source_citation if isinstance(source_citation, dict) else {},
            "event_text_for_match": _string_value(normalized, "event_text_for_match"),
            "content_hash": _string_value(item, "content_hash"),
        },
        contributes_to_classification=True,
        audit_only=False,
        quality_flags=_dedupe(quality_flags),
    )


def run_official_agenda_path(
    official_items: Iterable[Any],
    *,
    run_id: str = "day45-official-agenda",
    existing_events: list[RetrievedEventMatchCandidate] | None = None,
    extractor: EventSignalExtractor | None = None,
    resolver: EventResolverAgent | None = None,
) -> OfficialPathResult:
    """Path B: push official items through existing extraction and matching chain."""

    items = list(official_items)
    if not items:
        return OfficialPathResult(
            path_type="official_agenda",
            status="skipped",
            eligible_for_community_main_list=False,
            fallback_used=True,
            quality_flags=["official_pool_empty"],
        )

    source_signals = [
        normalized_item_to_source_signal(item, path_type="official_agenda") for item in items
    ]
    extractor = extractor or EventSignalExtractor(use_llm_globally=False)
    resolver = resolver or EventResolverAgent()
    extract_output = extractor.extract(
        ExtractEventSignalsInput(
            run_id=run_id,
            source_signals=source_signals,
            use_llm=False,
        )
    )
    quality_flags = list(extract_output.quality_flags)
    errors = list(extract_output.errors)
    if extract_output.status in {"failed", "skipped"} or not extract_output.event_signals:
        quality_flags.append(f"extract_event_signals_{extract_output.status}")
        return OfficialPathResult(
            path_type="official_agenda",
            status="failed" if extract_output.status == "failed" else "skipped",
            source_signals=source_signals,
            official_agenda_rank=official_agenda_rank_from_items(items),
            eligible_for_community_main_list=False,
            fallback_used=True,
            quality_flags=_dedupe(quality_flags),
            errors=errors,
        )

    source_refs = {
        signal.source_signal_id: SourceSignalMatchRef(
            source_signal_id=signal.source_signal_id,
            url=signal.url,
            platform_id=signal.item_id,
        )
        for signal in source_signals
    }
    match_output = resolver.resolve(
        MatchAndResolveEventsInput(
            run_id=run_id,
            event_signals=extract_output.event_signals,
            existing_events=existing_events or [],
            source_refs_by_signal_id=source_refs,
            match_config=EventMatchConfig(use_embedding=False),
        )
    )
    quality_flags.extend(match_output.quality_flags)
    errors.extend(match_output.errors)
    for resolution in match_output.event_resolutions:
        if resolution.action in {"candidate_review", "reject"}:
            quality_flags.append(f"event_resolution_{resolution.action}")
        if resolution.guardrail_status != "pass":
            quality_flags.append(f"guardrail_{resolution.guardrail_status}")

    if match_output.status == "succeeded" and not errors:
        status = "succeeded"
    elif match_output.event_resolutions:
        status = "partial"
    else:
        status = "failed"

    rank = official_agenda_rank_from_items(items)
    return OfficialPathResult(
        path_type="official_agenda",
        status=status,
        source_signals=source_signals,
        event_resolutions=match_output.event_resolutions,
        official_agenda_rank=rank,
        eligible_for_community_main_list=False,
        fallback_used=status != "succeeded",
        downstream_fields={
            "official_only": True,
            "official_agenda_rank": rank.model_dump(mode="json"),
            "extract_status": extract_output.status,
            "match_status": match_output.status,
        },
        quality_flags=_dedupe(quality_flags),
        errors=errors,
    )


def enrich_support_detail(
    result: OfficialSupportResult,
    *,
    official_query: OfficialQuery | None = None,
) -> OfficialSupportDetail:
    """Add Day 45 coverage and query fields to an OfficialSupportResult."""

    references = result.official_references
    source_names = [reference.source_name for reference in references]
    story_keys = {_story_key(reference.model_dump(mode="json")) for reference in references}
    independence_flags = _source_independence_flags(references)
    coverage_level = _coverage_level(
        source_count=len(set(source_names)),
        unique_story_count=len(story_keys),
        item_count=len(references),
    )
    detail = result.official_support_detail.model_copy(
        update={
            "official_item_count": len(references),
            "source_coverage_count": len(set(source_names)),
            "unique_story_count": len(story_keys),
            "official_coverage_level": coverage_level,
            "source_independence_flags": independence_flags,
            "official_query": official_query.model_dump(mode="json") if official_query else None,
        }
    )
    if detail.official_source_count == 0:
        detail.official_source_count = len(set(source_names))
    return detail


def official_agenda_rank_from_items(items: Iterable[Any]) -> OfficialAgendaRank:
    item_list = list(items)
    source_names = {
        _source_name(item)
        for item in item_list
        if _source_name(item)
    }
    story_keys = {_story_key(_item_reference_payload(item)) for item in item_list}
    statuses = {_string_value(item, "source_status") or "unknown" for item in item_list}
    source_status_rank = 0 if "use" in statuses else 1 if "fallback" in statuses else 2
    return OfficialAgendaRank(
        official_source_count=len(source_names),
        source_coverage_count=len(source_names),
        unique_story_count=len(story_keys),
        freshness_bucket=_freshness_bucket(item_list),
        authority_sources=sorted(source_names),
        source_status_rank=source_status_rank,
    )


def _has_community_presence(event: Any) -> bool:
    detail = _dict_value(event, "event_detail_json", "event_detail", default={}) or {}
    presence = detail.get("platform_presence") if isinstance(detail, dict) else None
    if isinstance(presence, dict):
        return any(
            bool(presence.get(key))
            for key in ("zhihu_topn", "zhihu_search", "weibo_topn", "weibo_cli")
        )
    citations = _dict_value(event, "source_citations_json", "source_citations", default=[]) or []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        if str(citation.get("source_type") or "").startswith("community"):
            return True
        if citation.get("platform") in {"zhihu", "weibo"}:
            return True
    return False


def _stable_item_id(item: Any, source_citation: dict[str, Any]) -> str | None:
    value = (
        _string_value(source_citation, "item_id")
        or _string_value(item, "item_id")
        or _string_value(item, "external_id")
        or _string_value(item, "url")
        or _string_value(item, "content_hash")
        or _string_value(item, "id")
    )
    return value


def _source_signal_id(path_type: str, item_id: str, title: str) -> str:
    digest = hashlib.sha256(f"{path_type}:{item_id}:{title}".encode()).hexdigest()[:16]
    return f"source-sig-{digest}"


def _generated_item_id(source_id: str, title: str, url: str | None) -> str:
    digest = hashlib.sha256(f"{source_id}:{title}:{url or ''}".encode()).hexdigest()[:16]
    return f"official-item-{digest}"


def _source_name(item: Any) -> str | None:
    source = getattr(item, "source", None)
    citation = _dict_value(item, "source_citation_json", "source_citation", default={}) or {}
    return (
        _string_value(item, "source_name")
        or _string_value(source, "name")
        or _string_value(citation, "source_name")
        or _string_value(item, "source_id")
    )


def _item_reference_payload(item: Any) -> dict[str, Any]:
    return {
        "item_id": _string_value(item, "id", "item_id", "external_id"),
        "title": _string_value(item, "title"),
        "url": _string_value(item, "url"),
        "content_hash": _string_value(item, "content_hash"),
    }


def _story_key(payload: dict[str, Any]) -> str:
    for key in ("content_hash", "url", "item_id"):
        value = payload.get(key)
        if value:
            return f"{key}:{str(value).strip().lower()}"
    title = str(payload.get("title") or "").strip().lower()
    normalized_title = "".join(title.split())
    return f"title:{normalized_title}"


def _source_independence_flags(references: list[Any]) -> list[str]:
    if not references:
        return []
    keys = [_story_key(reference.model_dump(mode="json")) for reference in references]
    flags: list[str] = []
    if len(keys) != len(set(keys)):
        flags.append("exact_duplicate")
    if len({reference.source_name for reference in references}) > 1:
        flags.append("source_coverage")
    if len(set(keys)) > 1:
        flags.append("independent_story")
    elif len(references) > 1:
        flags.append("same_story_cluster")
    return _dedupe(flags)


def _coverage_level(*, source_count: int, unique_story_count: int, item_count: int) -> str:
    if item_count == 0:
        return "none"
    if source_count <= 1:
        return "single_source"
    if unique_story_count <= 1:
        return "multi_source_duplicate"
    return "multi_source_independent"


def _freshness_bucket(items: list[Any]) -> str:
    published_values = [_datetime_value(item, "published_at") for item in items]
    published_values = [value for value in published_values if value is not None]
    if not published_values:
        return "unknown"
    newest = max(published_values)
    now = datetime.now(newest.tzinfo)
    hours = abs((now - newest).total_seconds()) / 3600
    if hours <= 24:
        return "24h"
    if hours <= 72:
        return "72h"
    if hours <= 168:
        return "7d"
    return "stale"


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


def _datetime_string(item: Any, *keys: str) -> str | None:
    value = _dict_value(item, *keys)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _datetime_value(item: Any, *keys: str) -> datetime | None:
    value = _dict_value(item, *keys)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
