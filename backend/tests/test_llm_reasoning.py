"""Offline transports only. No credentials, paid collection or provider calls."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from app.agents.analysis import HotspotAnalysisAgent
from app.agents.event_extractor import EventSignalExtractor
from app.agents.event_matcher import EventMatcher
from app.agents.event_resolver import EventResolverAgent
from app.core.config import settings
from app.schemas import (
    EventMatchConfig,
    EventSignal,
    ExtractEventSignalsInput,
    MatchAndResolveEventsInput,
    RetrievedEventMatchCandidate,
    SourceSignal,
)
from app.schemas.analysis import HotspotAnalysisInput
from app.services import llm_client
from app.services.llm_client import LLMFailure, reasoning_mode, reasoning_scope
from app.services.llm_event_workflow import evaluate_relevance

TITLE = "甲公司发布新机器人"


def source():
    return SourceSignal(
        source_signal_id="s1",
        item_id="1",
        source_id="zhihu_hot_list",
        source_type="community_hotlist",
        source_status="use",
        source_origin="official_api",
        platform="zhihu",
        signal_role="topic_discovery_signal",
        title=TITLE,
        fetched_at="2026-10-29T08:00:00+08:00",
    )


def signal():
    return EventSignal(
        event_signal_id="s",
        source_signal_ids=["s1"],
        title=TITLE,
        summary="甲公司今天发布新机器人。",
        event_text_for_match=TITLE,
        confidence=0.9,
    )


def candidate(event_id="event-1"):
    return RetrievedEventMatchCandidate(
        event_id=event_id, title=TITLE, summary="甲公司发布新机器人。"
    )


def same_pair(candidate_id="event-1", decision="same_event"):
    return {
        "candidate_id": candidate_id,
        "decision": decision,
        "evidence": [
            {"source_id": "incoming", "quote": TITLE},
            {"source_id": candidate_id, "quote": TITLE},
        ],
        "conflicts": [],
        "missing_information": [],
        "reason": "材料描述同一发布事件",
    }


def extraction():
    fact = {"value": "甲公司", "evidence": [{"source_id": "s1", "quote": "甲公司"}]}
    return {
        "subjects": [fact],
        "actions": [],
        "objects": [],
        "locations": [],
        "occurred_at": None,
        "keywords": [],
        "event_type": None,
        "event_specific": True,
        "missing_information": [],
    }


@pytest.fixture
def llm(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "deepseek_api_key", "unit-test-key-not-real")
    monkeypatch.setattr(settings, "llm_cache_path", str(tmp_path / "cache.db"))
    state = {"output": extraction(), "status": 200, "finish": "stop"}
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        if state.get("timeout"):
            raise httpx.ReadTimeout("secret-provider-error", request=request)
        body = state.get("content", json.dumps(state["output"], ensure_ascii=False))
        return httpx.Response(
            state["status"],
            json={
                "choices": [
                    {"finish_reason": state["finish"], "message": {"content": body}}
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 15,
                    "total_tokens": 35,
                },
            },
        )

    with reasoning_scope("llm"):
        runtime = llm_client.get_llm_client()
        runtime.http_client = httpx.Client(transport=httpx.MockTransport(handler))
        try:
            yield state, requests, runtime
        finally:
            runtime.http_client.close()


def test_llm_extraction_never_runs_rule_extractor(llm, monkeypatch):
    extractor = EventSignalExtractor()
    monkeypatch.setattr(
        extractor.rule_extractor, "extract", lambda *_: pytest.fail("rule backend used")
    )
    result = extractor.extract(
        ExtractEventSignalsInput(run_id="r", source_signals=[source()])
    )
    assert result.status == "succeeded"
    assert result.event_signals[0].entities == ["甲公司"]
    assert result.event_signals[0].action_terms == []  # No rule fields added back.
    assert result.event_signals[0].event_time_hint is None
    assert (
        result.event_signals[0].reasoning_detail["audit"]["usage"]["total_tokens"] == 35
    )
    assert len(llm[1]) == 1
    assert llm[1][0]["thinking"] == {"type": "disabled"}
    assert llm[1][0]["max_tokens"] == settings.llm_max_output_tokens


def test_hallucinated_quote_fails_without_rule_fallback(llm):
    llm[0]["output"]["subjects"][0]["evidence"][0]["quote"] = "材料不存在的公司"
    result = EventSignalExtractor().extract(
        ExtractEventSignalsInput(run_id="r", source_signals=[source()])
    )
    assert result.status == "failed" and not result.event_signals
    assert "llm_unverifiable_evidence" in result.errors[0]


@pytest.mark.parametrize("change", ["extra", "bad_time", "wrong_source"])
def test_strict_extraction_fields_are_validated(llm, change):
    data = llm[0]["output"]
    if change == "extra":
        data["event_id"] = "invented-id"
    elif change == "bad_time":
        data["occurred_at"] = {
            "value": "2026-10-29",
            "evidence": [{"source_id": "s1", "quote": TITLE}],
        }
    else:
        data["subjects"][0]["evidence"][0]["source_id"] = "nonexistent"
    result = EventSignalExtractor().extract(
        ExtractEventSignalsInput(run_id="r", source_signals=[source()])
    )
    assert result.status == "failed" and not result.event_signals


def test_llm_merge_does_not_use_embedding_reranker_or_legacy_thresholds(
    llm, monkeypatch
):
    llm[0]["output"] = {"judgements": [same_pair()]}
    monkeypatch.setattr(
        "app.agents.event_matcher.compare_many",
        lambda *a, **k: pytest.fail("legacy comparison used"),
    )
    output = EventResolverAgent().resolve(
        MatchAndResolveEventsInput(
            run_id="r",
            event_signals=[signal()],
            existing_events=[candidate()],
            match_config=EventMatchConfig(use_embedding=True, use_reranker=True),
        )
    )
    result = output.event_resolutions[0]
    assert result.action == "merge" and result.event_id == "event-1"
    assert result.matched_by == ["llm_event_judgement"]
    assert result.match_features_json.embedding_similarity is None
    assert result.match_features_json.rerank_score is None


@pytest.mark.parametrize("other", ["same_event", "insufficient_evidence"])
def test_competing_or_uncertain_candidate_never_auto_merges(llm, other):
    llm[0]["output"] = {"judgements": [same_pair(), same_pair("event-2", other)]}
    result = EventMatcher().match_event(
        signal=signal(),
        candidates=[candidate(), candidate("event-2")],
        config=EventMatchConfig(),
    )
    assert result.action == "candidate_review" and result.review_required


def test_all_distinct_candidates_create_independent_topic(llm):
    llm[0]["output"] = {"judgements": [same_pair(decision="related_but_distinct")]}
    result = EventMatcher().match_event(
        signal=signal(), candidates=[candidate()], config=EventMatchConfig()
    )
    assert result.action == "create" and result.matched_candidate_event_id is None


@pytest.mark.parametrize(
    "invalid", ["unknown_id", "missing", "duplicate", "missing_quote", "conflict"]
)
def test_invalid_merge_response_cannot_create_or_merge(llm, invalid):
    row = same_pair()
    rows = [row]
    if invalid == "unknown_id":
        row["candidate_id"] = "invented"
    elif invalid == "missing":
        rows = []
    elif invalid == "duplicate":
        rows = [row, row]
    elif invalid == "missing_quote":
        row["evidence"] = row["evidence"][:1]
    else:
        row["conflicts"] = ["动作不同"]
    llm[0]["output"] = {"judgements": rows}
    result = (
        EventResolverAgent()
        .resolve(
            MatchAndResolveEventsInput(
                run_id="r",
                event_signals=[signal()],
                existing_events=[candidate()],
            )
        )
        .event_resolutions[0]
    )
    assert result.action == "candidate_review"
    assert result.blocks_auto_analysis and result.blocks_publish


@pytest.mark.parametrize(
    "failure", [401, 429, 500, "timeout", "empty", "length", "json"]
)
def test_api_errors_are_sanitized_and_never_retried_or_fallback(llm, failure):
    if isinstance(failure, int):
        llm[0]["status"] = failure
    elif failure == "timeout":
        llm[0]["timeout"] = True
    elif failure == "empty":
        llm[0]["content"] = ""
    elif failure == "length":
        llm[0]["finish"] = "length"
    else:
        llm[0]["content"] = "not json unit-test-key-not-real"
    output = EventSignalExtractor().extract(
        ExtractEventSignalsInput(run_id="r", source_signals=[source()])
    )
    assert output.status == "failed" and not output.event_signals
    assert "unit-test-key" not in str(output.errors)
    assert "secret-provider" not in str(output.errors)
    assert len(llm[1]) == 1


def test_cache_reuses_valid_response_and_budget_stops_new_calls(llm, monkeypatch):
    extractor = EventSignalExtractor()
    data = ExtractEventSignalsInput(run_id="r", source_signals=[source()])
    assert extractor.extract(data).status == "succeeded"
    assert (
        extractor.extract(data).event_signals[0].reasoning_detail["audit"]["cache_hit"]
    )
    assert len(llm[1]) == 1
    monkeypatch.setattr(settings, "llm_max_calls_per_run", 1)
    llm[0]["output"] = {"judgements": [same_pair()]}
    result = EventMatcher().match_event(
        signal=signal(), candidates=[candidate()], config=EventMatchConfig()
    )
    assert result.action == "candidate_review" and len(llm[1]) == 1


def test_relevance_accepts_contextual_opinion_without_old_reranker(llm, monkeypatch):
    text = "这次机器人的设计很有意思，但还需要看实际效果。"
    llm[0]["output"] = {
        "decision": "opinion_on_event",
        "evidence": [
            {"source_id": "topic", "quote": TITLE},
            {"source_id": "result", "quote": text},
        ],
        "reason": "这是对本次发布的意见",
    }
    monkeypatch.setattr(
        "app.services.weibo_relevance.get_semantic_models",
        lambda: pytest.fail("reranker called"),
    )
    result = evaluate_relevance(TITLE, text, source="weibo_cli", user_query_id="q")
    assert result.decision == "accepted" and result.matched_by == ["llm_relevance"]
    assert result.reasoning_detail["judgement"]["decision"] == "opinion_on_event"
    old = evaluate_relevance(
        TITLE,
        text,
        source="weibo_cli",
        user_query_id="q",
        quality_flags=["historical_content_pollution"],
    )
    assert old.decision == "rejected" and len(llm[1]) == 1


@pytest.mark.parametrize(
    "source_status,expected", [("use", "supported"), ("fallback", "weak_supported")]
)
def test_official_support_uses_llm_without_old_comparisons(
    llm, monkeypatch, source_status, expected
):
    from app.services.official_support import match_official_support

    llm[0]["output"] = {"judgements": [same_pair("official:0")]}
    monkeypatch.setattr(
        "app.services.official_support.compare_many",
        lambda *a, **k: pytest.fail("old official comparison"),
    )
    now = datetime(2026, 10, 29, tzinfo=UTC)
    event = {"event_id": "event-1", "title": TITLE, "first_seen_at": now}
    item = {
        "title": TITLE,
        "summary": "甲公司发布新机器人。",
        "source_name": "中新网",
        "source_type": "official_news",
        "source_status": source_status,
        "signal_role": "event_signal",
        "url": "https://www.chinanews.com.cn/test",
        "published_at": now,
        "id": "i1",
    }
    result = match_official_support(event, [item], window_end=now)
    assert result.official_support_status == expected
    assert result.official_references[0].match_features["llm_decision"] == "same_event"


def test_no_key_fails_before_collection_and_mode_is_restored(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", None)
    monkeypatch.setattr(
        HotspotAnalysisAgent, "_run", lambda *a: pytest.fail("collection started")
    )
    with pytest.raises(LLMFailure, match="llm_api_key_missing"):
        HotspotAnalysisAgent().run(None, HotspotAnalysisInput(reasoning_mode="llm"))
    assert reasoning_mode() == "rules"


def test_llm_trial_preflight_does_not_require_old_matching_profile(llm, monkeypatch):
    from types import SimpleNamespace

    from app.services.overnight_test import preflight

    monkeypatch.setattr(settings, "weibo_cli_enabled", True)
    monkeypatch.setattr(settings, "matching_profile", "rules")
    monkeypatch.setattr(
        "app.services.weibo_heat_client.WeiboHeatClient.search_with_cli",
        lambda *a: SimpleNamespace(available=True),
    )
    db = SimpleNamespace(scalar=lambda *a: TITLE, commit=lambda: None)
    preflight(db)
    assert not llm[1]  # Configuration check does not contact the LLM provider.


def test_production_llm_startup_does_not_require_local_weights(monkeypatch):
    import logging
    from types import SimpleNamespace

    from app import main

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "admin_token", "x" * 32)
    monkeypatch.setattr(settings, "event_reasoning_mode", "llm")
    monkeypatch.setattr(settings, "semantic_model_dir", "/missing-models")
    monkeypatch.setattr(settings, "matching_profile", "hybrid_rerank")
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "SessionLocal", Database)
    monkeypatch.setattr(main, "initialize_recipients", lambda db: None)
    monkeypatch.setattr(main, "configure_logging", lambda: logging.getLogger(__name__))
    monkeypatch.setattr(
        main,
        "JobCoordinator",
        lambda: SimpleNamespace(start=lambda: None, stop=lambda: None),
    )

    # This lifespan performs no async I/O. Drive its coroutines directly so the
    # Windows sandbox need not create a socketpair just to construct an event loop.
    context = main.lifespan(SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(StopIteration):
        context.__aenter__().send(None)
    with pytest.raises(StopIteration):
        context.__aexit__(None, None, None).send(None)


def test_default_rule_backend_never_calls_llm(monkeypatch):
    monkeypatch.setattr(
        llm_client.DeepSeekJSONClient, "complete", lambda *a: pytest.fail("LLM called")
    )
    data = ExtractEventSignalsInput(run_id="r", source_signals=[source()], use_llm=True)
    result = EventSignalExtractor(use_llm_globally=True).extract(data)
    assert (
        result.status == "succeeded"
        and result.event_signals[0].reasoning_mode == "rules"
    )


def test_long_text_is_not_discarded_and_empty_long_text_falls_back():
    from app.services.weibo_heat_client import normalize_cli_status

    row = {
        "id": 1,
        "text": "短片段",
        "long_text": {"long_text_content": "完整正文包含关键事件经过"},
    }
    assert normalize_cli_status(row).text == "完整正文包含关键事件经过"
    row["long_text"] = {"long_text_content": "  "}
    assert normalize_cli_status(row).text == "短片段"
