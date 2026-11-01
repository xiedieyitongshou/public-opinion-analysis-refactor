"""Offline contracts for cost controls, identity reuse, concurrency and safe continuation."""

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_hotspot_analysis import environment as environment
from test_llm_reasoning import candidate, signal, source

from app.agents.event_matcher import EventMatcher
from app.core.config import settings
from app.db.init_db import init_db
from app.models import AgentTask
from app.models.briefing import AnalysisRun
from app.schemas import EventMatchConfig, ExtractEventSignalsInput, SourceSignalMatchRef
from app.schemas.platform_trend import PlatformTrendConfig, PlatformTrendInput
from app.services.analysis_checkpoint import task_checkpoint
from app.services.llm_candidates import select_candidates
from app.services.llm_client import (
    DeepSeekJSONClient,
    LLMBudgetExceeded,
    LLMFailure,
    cache_only,
    document,
    get_llm_client,
    reasoning_scope,
)
from app.services.llm_event_workflow import extract_signal, extract_signals, judge_pairs
from app.services.platform_trend import analyze_platform_trend
from app.services.request_usage import request_scope


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "deepseek_api_key", "offline-key")
    monkeypatch.setattr(settings, "llm_cache_path", str(tmp_path / "cache.db"))
    monkeypatch.setattr(settings, "llm_max_calls_per_run", 100)
    monkeypatch.setattr(settings, "llm_token_budget_per_run", 500000)
    monkeypatch.setattr(settings, "llm_max_concurrent_requests", 2)
    state = {"requests": [], "active": 0, "peak": 0, "same": set()}
    lock = threading.Lock()

    def handler(request):
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        with lock:
            state["requests"].append(payload)
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            if state.get("barrier"):
                state["barrier"].wait(timeout=3)
            if state.get("delay"):
                time.sleep(state["delay"])
            if "documents" in payload:
                doc = payload["documents"][0]
                output = {
                    "subjects": [
                        {
                            "value": doc["title"][:100],
                            "evidence": [{"source_id": doc["source_id"], "quote": doc["title"]}],
                        }
                    ],
                    "actions": [],
                    "objects": [],
                    "locations": [],
                    "occurred_at": None,
                    "keywords": [],
                    "event_type": None,
                    "event_specific": True,
                    "missing_information": [],
                }
            else:
                output = {"judgements": []}
                for doc in payload["candidates"]:
                    same = doc["source_id"] in state["same"]
                    output["judgements"].append(
                        {
                            "candidate_id": doc["source_id"],
                            "decision": "same_event" if same else "different_event",
                            "evidence": [
                                {
                                    "source_id": payload["incoming"]["source_id"],
                                    "quote": payload["incoming"]["title"],
                                },
                                {"source_id": doc["source_id"], "quote": doc["title"]},
                            ]
                            if same
                            else [],
                            "conflicts": [],
                            "missing_information": [],
                            "reason": "测试判断",
                            "uncertainty_kind": None,
                            "same_event_clues": [],
                        }
                    )
            body = "invalid-json" if state.get("invalid") else json.dumps(output)
            response = {"choices": [{"finish_reason": "stop", "message": {"content": body}}]}
            if not state.get("unknown_usage"):
                response["usage"] = {
                    "prompt_tokens": 20,
                    "completion_tokens": 15,
                    "total_tokens": 35,
                }
            return httpx.Response(200, json=response)
        finally:
            with lock:
                state["active"] -= 1

    with reasoning_scope("llm"):
        client = get_llm_client()
        client.http_client = httpx.Client(transport=httpx.MockTransport(handler))
        yield client, state
        client.http_client.close()


def docs(count, prefix="event"):
    return [
        document(f"{prefix}:{i}", f"候选事件{i}", f"这是第{i}项材料的具体正文。")
        for i in range(count)
    ]


def test_unique_source_identity_skips_llm_but_plain_title_does_not(runtime):
    client, state = runtime
    target = candidate().model_copy(update={"source_urls": ["https://www.zhihu.com/question/123"]})
    ref = SourceSignalMatchRef(source_signal_id="s1", url="https://www.zhihu.com/question/123")
    result = EventMatcher().match_event(
        signal=signal(),
        candidates=[target],
        config=EventMatchConfig(),
        source_refs_by_signal_id={"s1": ref},
    )
    assert result.action == "merge" and result.match_features_json.url_match
    assert client.calls == 0
    EventMatcher().match_event(signal=signal(), candidates=[candidate()], config=EventMatchConfig())
    assert len(state["requests"]) == 1


def test_competing_identity_owners_are_reviewed_without_guessing(runtime):
    first = candidate().model_copy(update={"source_urls": ["https://www.zhihu.com/question/123"]})
    second = first.model_copy(update={"event_id": "other"})
    ref = SourceSignalMatchRef(source_signal_id="s1", url=first.source_urls[0])
    result = EventMatcher().match_event(
        signal=signal(),
        candidates=[first, second],
        config=EventMatchConfig(),
        source_refs_by_signal_id={"s1": ref},
    )
    assert result.action == "candidate_review" and runtime[0].calls == 0


def test_extraction_reuses_content_and_rebinds_new_observation_ids(runtime):
    original = source()
    first = extract_signal(original, "r1")
    updated = original.model_copy(
        update={
            "source_signal_id": "new-source",
            "item_id": "2",
            "fetched_at": "2026-10-29T10:00:00+08:00",
            "raw_metrics": {"likes": 1000},
        }
    )
    second = extract_signal(updated, "r2")
    assert len(runtime[1]["requests"]) == 1
    assert first.source_signal_ids != second.source_signal_ids
    assert (
        second.reasoning_detail["facts"]["subjects"][0]["evidence"][0]["source_id"] == "new-source"
    )
    changed = updated.model_copy(update={"title": "甲公司发布新机器人并公布新进展"})
    extract_signal(changed, "r3")
    assert len(runtime[1]["requests"]) == 2


def test_relative_time_uses_reference_date_in_content_cache(runtime):
    original = source().model_copy(update={"title": "甲公司今天发布新机器人"})
    extract_signal(original, "r1")
    extract_signal(original.model_copy(update={"fetched_at": "2026-10-30T08:00:00+08:00"}), "r2")
    assert len(runtime[1]["requests"]) == 2


def test_pair_cache_survives_batch_reorder_owner_id_changes_and_new_candidates(runtime):
    client, state = runtime
    left = document("incoming", "新事件", "这是事件正文")
    candidates = docs(5)
    judge_pairs(left, candidates, client=client)
    assert len(state["requests"]) == 2
    rebound = [{**row, "source_id": "new:" + str(i)} for i, row in enumerate(candidates)]
    rows = judge_pairs(left, list(reversed(rebound)), client=client)
    assert len(state["requests"]) == 2
    assert [row.candidate_id for row, _ in rows] == [row["source_id"] for row in reversed(rebound)]
    judge_pairs(left, [*rebound, document("new:6", "新增报道", "新的正文材料")], client=client)
    assert len(state["requests"]) == 3 and len(state["requests"][-1]["candidates"]) == 1


def test_parallel_batches_keep_request_scope_and_limit(runtime, monkeypatch):
    client, state = runtime
    monkeypatch.setattr(settings, "llm_candidates_per_request", 1)
    state["barrier"] = threading.Barrier(2)
    with request_scope("scope", {"deepseek": 4}, 30) as scope:
        rows = judge_pairs(document("incoming", "新事件"), docs(4), client=client)
    assert len(rows) == 4 and state["peak"] == 2
    assert scope.counts["deepseek"] == 4 and len(scope.records) == 4
    assert all(r["run_id"] == "scope" for r in scope.records)


def test_token_budget_blocks_before_http_and_is_not_reset_by_new_client(runtime, monkeypatch):
    client, state = runtime
    monkeypatch.setattr(settings, "llm_token_budget_per_run", 1000)
    with pytest.raises(LLMBudgetExceeded, match="llm_token_budget"):
        judge_pairs(document("incoming", "新事件"), docs(1), client=client)
    assert not state["requests"]
    monkeypatch.setattr(settings, "llm_token_budget_per_run", 500000)
    monkeypatch.setattr(settings, "llm_max_calls_per_run", 1)
    judge_pairs(document("incoming", "新事件"), docs(1), client=client)
    other = DeepSeekJSONClient(http_client=client.http_client, run_id=client.run_id)
    with pytest.raises(LLMBudgetExceeded, match="llm_call_budget"):
        judge_pairs(document("incoming", "另一事件"), docs(1), client=other)
    assert len(state["requests"]) == 1


def test_failure_usage_is_recorded_and_unknown_usage_keeps_reservation(runtime):
    client, state = runtime
    state["invalid"] = True
    with pytest.raises(LLMFailure, match="llm_invalid_response"):
        judge_pairs(document("incoming", "事件"), docs(1), client=client)
    summary = client.budget.summary()
    assert summary["reported_tokens"] == 35 and summary["requests"] == 1
    state["unknown_usage"] = True
    with pytest.raises(LLMFailure):
        judge_pairs(document("incoming", "另一事件"), docs(1), client=client)
    summary = client.budget.summary()
    assert summary["charged_tokens"] > 35
    assert summary["tasks"]["event_comparison"]["unknown_usage"] == 1


def test_cache_only_never_triggers_a_paid_report_rebuild(runtime):
    client, state = runtime
    left = document("incoming", "事件")
    judge_pairs(left, docs(1), client=client)
    with cache_only():
        judge_pairs(left, docs(1), client=client)
        with pytest.raises(LLMFailure, match="llm_cache_miss"):
            judge_pairs(left, docs(2), client=client)
    assert len(state["requests"]) == 1


def test_sparse_dense_recall_limits_candidates_and_unavailable_dense_is_explicit(monkeypatch):
    from app.services import matching_engine
    from app.services.semantic_models import SemanticUnavailable

    monkeypatch.setattr(settings, "llm_candidate_limit", 20)
    monkeypatch.setattr(settings, "llm_retrieval_use_embedding", True)
    monkeypatch.setattr(
        matching_engine,
        "get_semantic_models",
        lambda: SimpleNamespace(
            encode=lambda texts: np.tile(np.array([1.0, 0.0]), (len(texts), 1))
        ),
    )
    selected, audit = select_candidates(document("incoming", "候选事件2"), docs(40))
    assert len(selected) == 20 and audit["pool_count"] == 40
    assert "event:2" in audit["candidate_ids"] and audit["method"] == "bm25_dense_rrf"

    def unavailable():
        raise SemanticUnavailable("embedding_load_failed")

    monkeypatch.setattr(matching_engine, "get_semantic_models", unavailable)
    with pytest.raises(LLMFailure, match="llm_candidate_retrieval_unavailable"):
        select_candidates(document("incoming", "事件"), docs(40))


def test_extraction_checkpoint_restarts_only_remaining_sources(runtime, monkeypatch):
    engine = create_engine("sqlite://")
    init_db(engine)
    from app.services import llm_event_workflow

    original = llm_event_workflow.extract_signal
    second = source().model_copy(update={"source_signal_id": "s2", "title": "另一话题"})
    data = ExtractEventSignalsInput(run_id="resume", source_signals=[source(), second])

    def stop_second(value, run_id, **kwargs):
        if value.source_signal_id == "s2":
            raise LLMBudgetExceeded("llm_token_budget_exceeded")
        return original(value, run_id, **kwargs)

    monkeypatch.setattr(llm_event_workflow, "extract_signal", stop_second)
    with Session(engine) as db:
        first = AgentTask(
            task_type="extract_event_signals",
            status="running",
            plan_id="p1",
            input_json=data.model_dump(mode="json"),
        )
        db.add(first)
        db.commit()
        with task_checkpoint(db, first, enabled=True), pytest.raises(LLMBudgetExceeded):
            extract_signals(data)
        first.status = "failed"
        db.commit()
        assert len(first.output_json["event_signals"]) == 1
        monkeypatch.setattr(llm_event_workflow, "extract_signal", original)
        nxt = AgentTask(
            task_type=first.task_type, status="running", plan_id="p2", input_json=first.input_json
        )
        db.add(nxt)
        db.commit()
        with task_checkpoint(db, nxt, enabled=True):
            output = extract_signals(data)
        assert len(output.event_signals) == 2 and len(runtime[1]["requests"]) == 2
    engine.dispose()


def test_stale_score_is_displayable_without_claiming_current_heat():
    from test_day47_platform_trend import END, observation

    from app.services.briefing_render import trend_label

    obs = observation("weibo", 10, score=0.7)
    result = analyze_platform_trend(
        PlatformTrendInput(
            event_id="event-1",
            platform="weibo",
            run_id="r",
            window_start=END - timedelta(hours=24),
            window_end=END,
            observations=[obs],
            config=PlatformTrendConfig(expected_interval_minutes=45),
        )
    )
    assert result.latest_platform_heat is None and result.current_topn_present is None
    assert result.last_observed_heat.platform_score == 0.7
    card = SimpleNamespace(platform_heat_analysis=SimpleNamespace(platforms={"weibo": result}))
    assert "最近采集热度 0.700" in trend_label(card, "weibo")
    assert "非当前值" in trend_label(card, "weibo")


def test_full_pipeline_budget_pause_and_resume_never_recollects(runtime, monkeypatch, environment):
    from test_hotspot_analysis import request

    from app.services import llm_client
    from app.services.briefing_jobs import execute_analysis, resume_analysis

    db, collector, _ = environment
    monkeypatch.setattr(settings, "zhihu_hot_list_provider", "official")
    monkeypatch.setattr(settings, "official_search_enabled", False)
    monkeypatch.setattr(llm_client, "DeepSeekJSONClient", lambda **kwargs: runtime[0])
    monkeypatch.setattr(settings, "llm_max_calls_per_run", 1)
    count = [0]
    original = collector.fetch_hot_list

    def fetch(*args, **kwargs):
        count[0] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(collector, "fetch_hot_list", fetch)
    data = request("llm-pause-resume", collector.time).model_copy(
        update={"reasoning_mode": "llm", "official_search_enabled": False}
    )
    first = execute_analysis(db, data)
    assert first.status == "partial" and "analysis_incomplete" in first.output_json["quality_flags"]
    assert first.report_id is None and count[0] == 1
    assert len(runtime[1]["requests"]) == 1
    with pytest.raises(ValueError, match="预算"):
        resume_analysis(db, data.run_id)
    monkeypatch.setattr(settings, "llm_max_calls_per_run", 100)
    from app.models import Item

    updated_item = db.scalar(select(Item).order_by(Item.id))
    original_time = updated_item.fetched_at
    updated_item.fetched_at = original_time + timedelta(hours=1)
    db.commit()
    with pytest.raises(ValueError, match="更新观测"):
        resume_analysis(db, data.run_id)
    updated_item.fetched_at = original_time
    # Late completion of this very observation is not a newer collection.
    original_end = first.window_end
    first.window_end = original_time - timedelta(seconds=1)
    db.commit()
    resume_analysis(db, data.run_id)
    first.window_end = original_end
    db.commit()
    final = execute_analysis(db, data)
    assert final.status != "failed", final.error
    assert "analysis_incomplete" not in final.output_json["quality_flags"], final.output_json
    assert count[0] == 1 and len(runtime[1]["requests"]) == 3, [
        {
            "decisions": [
                (r["action"], r["reason"]) for r in task.output_json.get("event_resolutions", [])
            ],
            "errors": task.output_json.get("errors"),
        }
        for task in db.scalars(
            select(AgentTask).where(AgentTask.task_type == "match_and_resolve_events")
        )
    ]
    assert len(final.output_json["classifications"]) == 2


def test_supervisor_recovers_collection_and_checkpoints_without_calls(runtime):
    from app.services.briefing_jobs import JobCoordinator

    engine = create_engine("sqlite://")
    init_db(engine)
    with Session(engine) as db:
        row = AnalysisRun(
            run_id="killed",
            status="running",
            started_at=datetime.now(UTC),
            input_json={"reasoning_mode": "llm"},
        )
        db.add(row)
        db.add_all(
            [
                AgentTask(
                    task_type="fetch_source_items",
                    plan_id="p",
                    status="succeeded",
                    input_json={"run_id": "killed"},
                    output_json={
                        "results": [{"source_id": "zhihu_hot_list", "status": "succeeded"}]
                    },
                ),
                AgentTask(
                    task_type="prepare_source_signals",
                    plan_id="p",
                    status="succeeded",
                    input_json={"run_id": "killed"},
                    output_json={"window_end": "2026-10-31T01:00:00Z"},
                ),
                AgentTask(
                    task_type="classify_events",
                    plan_id="p",
                    status="running",
                    input_json={"run_id": "killed"},
                    output_json={"classifications": []},
                ),
            ]
        )
        db.commit()
        JobCoordinator()._fail_run(db, "killed", "wall_clock_timeout")
        assert row.status == "partial" and row.collection_json["results"]
        assert row.output_json["window_end"].startswith("2026-10-31T01:00:00")
        assert row.error == "wall_clock_timeout" and not runtime[1]["requests"]
    engine.dispose()


@pytest.mark.parametrize(
    "incoming_url,candidate_url",
    [
        ("https://weibo.com/", "https://weibo.com/"),
        ("https://weibo.com/123/AbCd", "https://weibo.com/123/abcd"),
    ],
)
def test_generic_or_case_distinct_urls_do_not_short_circuit(runtime, incoming_url, candidate_url):
    target = candidate().model_copy(update={"source_urls": [candidate_url]})
    ref = SourceSignalMatchRef(source_signal_id="s1", url=incoming_url)
    result = EventMatcher().match_event(
        signal=signal(),
        candidates=[target],
        config=EventMatchConfig(),
        source_refs_by_signal_id={"s1": ref},
    )
    assert not result.match_features_json.url_match
    assert len(runtime[1]["requests"]) == 1
