"""Adapters to the production code. Only external acquisition is replaced by fixtures."""

from collections import Counter
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.agents.event_extractor import EventSignalExtractor
from app.agents.event_resolver import EventResolverAgent
from app.agents.normalizer import NormalizerAgent
from app.evaluation.contracts import EvaluationSuite
from app.schemas.classification import HotspotClassificationInput
from app.schemas.collectors import (
    CollectorRunConfig,
    CrawlValidationResult,
    FetchSourceItemsInput,
    FetchSourceItemsOutput,
    NormalizeRawItemsInput,
)
from app.schemas.matching import MatchAndResolveEventsInput, RetrievedEventMatchCandidate
from app.schemas.platform_heat import PlatformHeatSignalInput
from app.schemas.platform_trend import PlatformTrendConfig, PlatformTrendInput
from app.schemas.signals import ExtractEventSignalsInput
from app.services.analysis_interfaces import item_to_source_signal
from app.services.event_classification import classify_hotspot
from app.services.matching_profiles import event_config, official_config
from app.services.official_support import match_official_support
from app.services.platform_heat import score_platform_signals
from app.services.platform_trend import analyze_platform_trend

EVALUATION_PROFILE = ContextVar("evaluation_profile", default="rules")


def item(spec: dict, suite: EvaluationSuite) -> dict:
    value = deepcopy(suite.corpus[spec["ref"]]["item"] if "ref" in spec else spec["item"])
    value.update(deepcopy(spec.get("overrides", {})))
    return value


def normalize(raw: dict):
    output = NormalizerAgent().normalize(NormalizeRawItemsInput(items=[raw]))
    if not output.items:
        raise ValueError(f"normalization produced no item: {output.errors}")
    return output.items[0]


def extract(raw: dict, identity: str):
    normalized = normalize(raw)
    source = item_to_source_signal(normalized, item_id=identity)
    output = EventSignalExtractor(use_llm_globally=False).extract(
        ExtractEventSignalsInput(run_id="evaluation", source_signals=[source], use_llm=False)
    )
    if not output.event_signals:
        raise ValueError(f"extraction produced no signal: {output.errors}")
    return normalized, source, output.event_signals[0]


def normalization(data, suite):
    raw = item(data["item"], suite)
    result = normalize(raw)
    source = item_to_source_signal(result, item_id="fixture-item")
    repeated = normalize(result.model_dump(mode="json"))
    return {
        "title": result.title,
        "url": result.url,
        "published_at": result.published_at.isoformat() if result.published_at else None,
        "fetched_at": result.fetched_at.isoformat(),
        "hash_preserved": result.normalized.get("collector_content_hash")
        == raw.get("content_hash"),
        "idempotent_hash": result.content_hash == repeated.content_hash,
        "traceable": source.item_id == "fixture-item",
        "source_id": source.source_id,
    }


def matching(data, suite):
    left, left_source, left_signal = extract(item(data["left"], suite), "left")
    right, right_source, right_signal = extract(item(data["right"], suite), "right")
    candidate = RetrievedEventMatchCandidate(
        event_id="existing",
        title=left_signal.title,
        keywords=left_signal.keywords,
        entities=left_signal.entities,
        action_terms=left_signal.action_terms,
        event_type=left_signal.event_type,
        event_time_hint=left_signal.event_time_hint,
        first_seen_at=left.fetched_at,
        last_seen_at=left.fetched_at,
        event_text_for_match=left_signal.event_text_for_match,
        source_signal_ids=[left_source.source_signal_id],
        source_urls=[left.url],
        platform_ids=[f"{left.source_id}:{left.external_id or 'left'}"],
    )
    output = EventResolverAgent().resolve(
        MatchAndResolveEventsInput(
            run_id="evaluation",
            event_signals=[right_signal],
            existing_events=[candidate],
            match_config=event_config(EVALUATION_PROFILE.get()),
            source_refs_by_signal_id={
                right_source.source_signal_id: {
                    "source_signal_id": right_source.source_signal_id,
                    "url": right.url,
                    "platform_id": f"{right.source_id}:{right.external_id or 'right'}",
                }
            },
        )
    )
    resolution = output.event_resolutions[0]
    return {
        "same_event": (
            resolution.action == "merge"
            and not resolution.review_required
            and not resolution.blocks_auto_analysis
            and resolution.guardrail_status != "block"
        ),
        "action": resolution.action,
        "review_required": resolution.review_required,
        "guardrail_status": resolution.guardrail_status,
        "resolution": resolution.model_dump(mode="json"),
    }


def official_support(data, suite):
    normalized, _, signal = extract(item(data["event"], suite), "event")
    # Mirror the current persistence interface: extraction features are nested here.
    event = {
        "event_id": "event",
        "title": signal.title,
        "summary": normalized.summary,
        "primary_entity": next(iter(signal.entities), None),
        "keywords": signal.keywords,
        "first_seen_at": normalized.fetched_at,
        "last_seen_at": normalized.fetched_at,
        "event_detail": {
            "match_features": {
                "entities": signal.entities,
                "action_terms": signal.action_terms,
            }
        },
    }
    pool = [normalize(item(spec, suite)) for spec in data.get("official_items", [])]
    result = match_official_support(event, pool, official_config(EVALUATION_PROFILE.get()))
    return {
        "supported": result.official_support_status in {"supported", "weak_supported"},
        "status": result.official_support_status,
        "references": [ref.model_dump(mode="json") for ref in result.official_references],
        "candidate_comparisons": result.official_support_detail.candidate_comparisons,
    }


def classification(data, suite):
    return classify_hotspot(HotspotClassificationInput.model_validate(data)).model_dump(mode="json")


def heat(data, suite):
    if data["check"] == "within_category":
        return category_order(data, suite)
    groups = []
    for specs in data["groups"]:
        groups.append(
            score_platform_signals(
                [PlatformHeatSignalInput.model_validate(spec) for spec in specs],
                observed_at=datetime.fromisoformat(suite.window_end),
            )
        )
    if data["check"] == "ordering":
        scores = [group[0].platform_score if group else None for group in groups]
        order = sorted(range(len(scores)), key=lambda n: -(scores[n] or 0))
        return {"order": order, "scores": scores}
    scores = [score for group in groups for score in group]
    return {
        "score_count": len(scores),
        "has_numeric_score": any(score.platform_score is not None for score in scores),
        "has_primary_rank": any(score.primary_platform_rank is not None for score in scores),
        "platforms": sorted({score.platform for score in scores}),
        "rss_order_labeled": all(
            "list_position" in score.raw_metrics_used and "rank" not in score.raw_metrics_used
            for score in scores
            if score.platform == "weibo"
        ),
        "scores": [score.model_dump(mode="json") for score in scores],
    }


def category_order(data, suite):
    from app.models import Event
    from app.schemas.platform_trend import EventHeatAnalysis, PlatformTrendResult
    from app.services.platform_trend_assembly import rank_events_within_platform

    end = datetime.fromisoformat(suite.window_end)
    events = []
    for spec in data["events"]:
        base = {
            "event_id": spec["id"],
            "run_id": "order",
            "window_end": end,
            "window_start": end - timedelta(hours=24),
        }
        platforms = {}
        for platform in ("zhihu", "weibo"):
            state = spec.get(platform, {})
            platforms[platform] = PlatformTrendResult(
                **base,
                platform=platform,
                current_topn_present=state.get("present"),
                continuous_topn_minutes=state.get("duration"),
                latest_platform_heat={
                    "event_id": spec["id"],
                    "platform": platform,
                    "score_status": "partial",
                    "platform_score": state.get("score"),
                    "primary_platform_rank": state.get("rank"),
                },
            )
        analysis = EventHeatAnalysis(**base, platforms=platforms, status="partial")
        events.append(
            Event(
                event_id=spec["id"],
                title=spec["id"],
                event_detail_json={
                    "priority_category": spec["category"],
                    "classification_detail": {
                        "run_id": "order",
                        "window_end": end.isoformat(),
                        "window_start": (end - timedelta(hours=24)).isoformat(),
                    },
                    "platform_heat_analysis": analysis.model_dump(mode="json"),
                },
            )
        )
    ordered = rank_events_within_platform(
        events, platform=data["platform"], category=data["category"]
    )
    return {"order": [event.event_id for event in ordered]}


def trend(data, suite):
    return analyze_platform_trend(PlatformTrendInput.model_validate(data)).model_dump(mode="json")


def collection(data, suite):
    """Exercise actual parsing/mapping with a mocked transport, never a live endpoint."""
    from app.collectors.official import OfficialRSSCollector, OfficialRSSSourceConfig
    from app.collectors.zhihu import ZhihuHotListCollector
    from app.services.zhihu_client import ZhihuHotListResult

    if data["source"] == "zhihu":

        def fetch_hot_list(limit):
            if data.get("outage"):
                raise TimeoutError("fixture timeout")
            return ZhihuHotListResult(
                total=data.get("total", len(data["items"])),
                fetched_at=datetime.fromisoformat(suite.window_end),
                items=data["items"],
                raw_payload={"Items": data["items"]},
            )

        collector = ZhihuHotListCollector(client=SimpleNamespace(fetch_hot_list=fetch_hot_list))
        result = collector.collect(
            CollectorRunConfig(
                source_id="zhihu_hot_list",
                limit=5,
                validate_only=False,
                dry_run=True,
            )
        )
    else:

        def transport(request):
            if data.get("outage"):
                raise httpx.ConnectTimeout("fixture timeout", request=request)
            return httpx.Response(data.get("http_status", 200), text=data.get("xml", ""))

        config = OfficialRSSSourceConfig(
            source_id="chinanews_scroll_rss",
            source_name="fixture",
            platform="chinanews",
            rss_url="https://fixture.invalid/rss",
            channel="news",
            source_status="use",
            authority_weight=0.75,
        )
        with httpx.Client(transport=httpx.MockTransport(transport)) as client:
            collector = OfficialRSSCollector(config, http_client=client)
            result = collector.collect(CollectorRunConfig(source_id=config.source_id, limit=5))
    return {
        "status": result.status,
        "returned_count": result.returned_count,
        "normalized_count": result.normalized_count,
        "list_complete": result.list_complete,
        "has_error": bool(result.error_message),
        "quality_flags": result.quality_flags,
        "missing_published_count": sum(
            not value.get("published_at") for value in result.normalized_items
        ),
    }


def replay_round(
    raw_items: list[dict],
    run_id: str,
    window_end: str,
    failures: list[str],
    *,
    complete: bool = False,
):
    by_source: dict[str, list[dict]] = {}
    for raw in raw_items:
        by_source.setdefault(raw["source_id"], []).append(raw)
    for source in failures:
        by_source.setdefault(source, [])
    results = []
    for source, values in by_source.items():
        failed = source in failures
        observed = values[0]["fetched_at"] if values else window_end
        results.append(
            CrawlValidationResult(
                source_id=source,
                run_id=run_id,
                status="failed" if failed else "succeeded",
                validate_only=False,
                dry_run=False,
                returned_count=len(values),
                normalized_count=len(values),
                normalized_items=values,
                observation_id=f"{run_id}:{source}",
                observed_at=observed,
                requested_limit=5,
                list_complete=(
                    not failed
                    and complete
                    and source in {"zhihu_hot_list", "weibo_rsshub_hot_search"}
                ),
                topn_scope="top5:skip=0",
                sampling_signature="fixture-v1",
                error_message="fixture outage" if failed else None,
            )
        )
    return FetchSourceItemsOutput(
        status=("partial" if raw_items else "failed") if failures else "succeeded",
        run_id=run_id,
        validate_only=False,
        dry_run=False,
        requested_source_ids=list(by_source),
        results=results,
        items=raw_items,
        unavailable_source_ids=failures,
    )


def run_workflow(rounds: list[tuple[FetchSourceItemsOutput, str]], *, retry: bool = False):
    from app.agents.analysis import HotspotAnalysisAgent
    from app.db.init_db import init_db
    from app.models import AgentTask, AgentToolCall, Event, EventSnapshot, HumanReviewTask, Item
    from app.models.business import PlatformScore
    from app.schemas.analysis import HotspotAnalysisInput
    from app.tools import ToolRegistry, default_tool_registry

    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    fetch_calls = 0

    def fetch(request, context):
        nonlocal fetch_calls
        fetch_calls += 1
        return active.model_copy(deep=True)

    registry = ToolRegistry()
    for definition in default_tool_registry.list_tools():
        if definition.name == "fetch_source_items":
            definition = replace(definition, handler=fetch)
        registry.register(definition)
    try:
        with Session(engine) as db:
            outputs = []
            for active, window_end in rounds:
                request = HotspotAnalysisInput(
                    run_id=active.run_id,
                    window_end=window_end,
                    use_llm=False,
                    matching_profile=EVALUATION_PROFILE.get(),
                    official_search_enabled=False,
                    collection=FetchSourceItemsInput(
                        source_ids=active.requested_source_ids,
                        limit=max(
                            1,
                            min(
                                100,
                                max(
                                    (r.requested_limit for r in active.results),
                                    default=20,
                                ),
                            ),
                        ),
                        per_source_limits={
                            r.source_id: r.requested_limit
                            for r in active.results
                            if r.requested_limit > 0
                        },
                        validate_only=False,
                        dry_run=False,
                    ),
                    trend_configs={
                        p: PlatformTrendConfig(expected_interval_minutes=120)
                        for p in ("zhihu", "weibo")
                    },
                )
                outputs.append(HotspotAnalysisAgent(registry).run(db, request))
            models = [Item, Event, PlatformScore, EventSnapshot, HumanReviewTask]

            def counts():
                return {
                    m.__tablename__: db.scalar(select(func.count()).select_from(m)) for m in models
                }

            before = counts()
            if retry:
                outputs.append(HotspotAnalysisAgent(registry).run(db, request))
            result = outputs[-1]
            tasks = list(db.scalars(select(AgentTask)))
            tool_calls = list(db.scalars(select(AgentToolCall)))
            events = list(db.scalars(select(Event)))
            items = list(db.scalars(select(Item)))
            classifications = {
                v.event_id: v.classification.priority_category for v in result.classifications
            }
            categories = {event.title: classifications.get(event.event_id) for event in events}
            analyses = {
                event.title: next(
                    (
                        v.platforms["zhihu"].model_dump(mode="json")
                        for v in result.analyses
                        if v.event_id == event.event_id
                    ),
                    None,
                )
                for event in events
            }
            return {
                "status": result.status,
                "counts": counts(),
                "event_count": len(events),
                "review_count": before[HumanReviewTask.__tablename__],
                "idempotent": before == counts() if retry else None,
                "fetch_calls": fetch_calls,
                "task_count": len(tasks),
                "tool_call_count": len(tool_calls),
                "tool_statuses": dict(Counter(call.status for call in tool_calls)),
                "task_statuses": dict(Counter(task.status for task in tasks)),
                "categories_by_title": categories,
                "zhihu_analysis_by_title": analyses,
                "category_counts": dict(Counter(v for v in categories.values() if v)),
                "traceable": all(
                    (row.source_citation_json or {}).get("item_id") == str(row.id) for row in items
                ),
                "all_zhihu_unknown": bool(result.analyses)
                and all(
                    value.platforms["zhihu"].trend_status == "unknown"
                    and value.platforms["zhihu"].current_topn_present is None
                    for value in result.analyses
                ),
                "errors": result.errors,
            }
    finally:
        engine.dispose()


def workflow(data, suite):
    rounds = []
    if "collection" in data:
        rounds.append((FetchSourceItemsOutput.model_validate(data["collection"]), suite.window_end))
    for index, spec in enumerate(data.get("rounds", [])):
        time = spec.get("window_end", suite.window_end)
        raw = [item(value, suite) for value in spec.get("items", [])]
        rounds.append(
            (
                replay_round(
                    raw,
                    f"eval-round-{index}",
                    time,
                    spec.get("failures", []),
                    complete=spec.get("complete", False),
                ),
                time,
            )
        )
    result = run_workflow(rounds, retry=data.get("retry", False))
    if data.get("target_title"):
        result["target_category"] = result["categories_by_title"].get(data["target_title"])
        analysis = result["zhihu_analysis_by_title"].get(data["target_title"]) or {}
        result["target_trend"] = analysis.get("trend_status")
        result["target_duration"] = analysis.get("continuous_topn_minutes")
    return result


def retrieval(data, suite):
    from app.services.match_documents import MatchDocument
    from app.services.matching_engine import retrieve

    documents = [MatchDocument.model_validate(value["document"]) for value in data["candidates"]]
    indices, audit = retrieve(
        MatchDocument.model_validate(data["target"]),
        documents,
        limit=data.get("limit", 3),
        use_embedding=EVALUATION_PROFILE.get() != "rules",
    )
    ids = [data["candidates"][index]["id"] for index in indices]
    # Gold IDs are consulted only after retrieval, never passed to the retriever.
    relevant = set(data["relevant_ids"])
    return {
        "all_relevant_recalled": relevant.issubset(ids),
        "retrieved_ids": ids,
        "relevant_found": len(relevant.intersection(ids)),
        "relevant_total": len(relevant),
        "audit": audit,
    }


EVALUATORS = {
    function.__name__: function
    for function in (
        normalization,
        matching,
        official_support,
        classification,
        heat,
        trend,
        collection,
        workflow,
        retrieval,
    )
}


def evaluate(kind, data, suite, *, profile="rules"):
    # Any accidental real network operation is an evaluation error, not a paid/live test.
    with (
        patch("socket.socket.connect", side_effect=RuntimeError("network disabled in evaluation")),
        patch(
            "socket.socket.connect_ex", side_effect=RuntimeError("network disabled in evaluation")
        ),
    ):
        token = EVALUATION_PROFILE.set(profile)
        try:
            return EVALUATORS[kind](deepcopy(data), suite)
        finally:
            EVALUATION_PROFILE.reset(token)
