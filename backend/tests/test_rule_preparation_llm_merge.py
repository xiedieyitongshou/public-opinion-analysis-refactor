"""Rule preparation can feed the LLM without making extraction/relevance API calls."""

from types import SimpleNamespace

import pytest
from test_day38_event_extractor import source_signal

from app.agents.event_extractor import EventSignalExtractor, RuleEventExtractor
from app.core.config import Settings, settings
from app.schemas import ExtractEventSignalsInput
from app.schemas.analysis import HotspotAnalysisInput
from app.services.analysis_checkpoint import version
from app.services.llm_client import (
    LLMFailure,
    extraction_mode,
    get_llm_client,
    reasoning_mode,
    reasoning_scope,
    require_llm_configuration,
    weibo_relevance_mode,
)
from app.services.search_enrichment_filter import evaluate_search_enrichment


def forbidden(*args, **kwargs):
    pytest.fail("Unexpected LLM operation during rule preparation")


def item(title, body=""):
    return {
        "source_id": "weibo_rsshub_hot_search", "source_name": "微博",
        "source_type": "community_hotlist", "source_status": "use", "source_origin": "rsshub",
        "platform": "weibo", "signal_role": "topic_discovery_signal", "title": title,
        "content_text": body, "url": "https://s.weibo.com/weibo?q=test",
        "fetched_at": "2026-10-31T08:00:00+08:00",
    }


@pytest.fixture
def mixed(monkeypatch):
    monkeypatch.setattr(settings, "event_reasoning_mode", "llm")
    monkeypatch.setattr(settings, "event_extraction_mode", "rules")
    monkeypatch.setattr(settings, "weibo_cli_relevance_mode", "rules")
    monkeypatch.setattr("app.services.llm_event_workflow.extract_signals", forbidden)
    monkeypatch.setattr("app.services.llm_event_workflow.evaluate_relevance", forbidden)


def test_rule_features_keep_source_body_and_do_not_guess_occurrence_time(mixed):
    body = "10月31日，某品牌公司宣布召回涉事产品，消费者可申请免费更换。"
    cli_body = "补充正文包含具体产品批次和召回范围。"
    source = source_signal(platform_features={"llm_documents": [
        {"source_id": "rss", "title": "标题", "body": body},
        {"source_id": "cli", "title": "", "body": cli_body},
    ]})
    rule = RuleEventExtractor().extract(source)
    result = EventSignalExtractor().extract(
        ExtractEventSignalsInput(run_id="mixed", source_signals=[source]),
    )
    assert result.status == "succeeded"
    signal = result.event_signals[0]
    assert signal.entities == rule.entities and signal.keywords == rule.keywords
    assert signal.action_terms == rule.action_terms
    assert signal.summary == body + "\n" + cli_body
    assert body in signal.event_text_for_match and cli_body in signal.event_text_for_match
    assert signal.has_accepted_cli_context and signal.event_time_hint is None
    assert signal.reasoning_detail["extraction_backend"] == "rules"
    assert reasoning_mode() == "llm"


def test_cli_uses_original_rule_reranker_thresholds_in_llm_run(mixed, monkeypatch):
    from app.services import weibo_relevance

    seen = []

    def rerank(pairs):
        seen.extend(pairs)
        return [0.99]

    monkeypatch.setattr(weibo_relevance, "get_semantic_models",
                        lambda: SimpleNamespace(rerank=rerank))
    monkeypatch.setattr(settings, "matching_profile", "hybrid_rerank")
    relation = evaluate_search_enrichment(
        item("某品牌公司召回产品"),
        item("正文", "#某品牌公司召回产品# 企业已公布召回批次和免费更换办法。"),
        source="weibo_cli", user_query_id="saved-topic",
    )
    assert relation.decision == "accepted" and "body_reranker" in relation.matched_by
    assert len(seen) == 1 and "#" not in seen[0][1]


def test_rules_cli_still_rejects_conflicts_without_request(mixed, monkeypatch):
    from app.services import weibo_relevance

    monkeypatch.setattr(weibo_relevance, "explicit_conflicts", lambda *args: ["model_conflict"])
    monkeypatch.setattr(weibo_relevance, "get_semantic_models", forbidden)
    relation = evaluate_search_enrichment(
        item("某手机发布"), item("正文", "另一型号手机发布"),
        source="weibo_cli", user_query_id="saved-topic",
    )
    assert relation.decision == "rejected"


def test_stage_scope_is_restored_and_historical_override_is_explicit(mixed, monkeypatch):
    monkeypatch.setattr("app.services.llm_client.DeepSeekJSONClient", lambda: None)
    assert extraction_mode() == weibo_relevance_mode() == "rules"
    with reasoning_scope("llm", extraction_mode="llm", weibo_cli_relevance_mode="llm"):
        assert extraction_mode() == weibo_relevance_mode() == "llm"
    assert extraction_mode() == weibo_relevance_mode() == "rules"


def test_queued_input_snapshots_settings_and_checkpoint_changes(mixed, monkeypatch):
    data = HotspotAnalysisInput()
    assert data.reasoning_mode == "llm" and data.extraction_mode == "rules"
    assert data.weibo_cli_relevance_mode == "rules"
    old = version()
    monkeypatch.setattr(settings, "event_extraction_mode", "llm")
    assert version() != old
    assert data.extraction_mode == "rules"


def test_legacy_configuration_inherits_global_mode(monkeypatch):
    monkeypatch.setattr(settings, "event_reasoning_mode", "llm")
    monkeypatch.setattr(settings, "event_extraction_mode", "inherit")
    monkeypatch.setattr(settings, "weibo_cli_relevance_mode", "inherit")
    assert extraction_mode() == weibo_relevance_mode() == "llm"
    assert Settings(_env_file=None).event_extraction_mode == "inherit"


def test_explicit_llm_stage_requires_configuration_before_paid_collection(monkeypatch):
    monkeypatch.setattr(settings, "event_reasoning_mode", "rules")
    monkeypatch.setattr(settings, "event_extraction_mode", "rules")
    monkeypatch.setattr(settings, "weibo_cli_relevance_mode", "llm")
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    with pytest.raises(LLMFailure, match="llm_api_key_missing"):
        require_llm_configuration()


def test_explicit_llm_stage_shares_one_client_inside_rules_scope(monkeypatch):
    runtime = object()
    monkeypatch.setattr("app.services.llm_client.DeepSeekJSONClient", lambda: runtime)
    with reasoning_scope("rules", extraction_mode="rules", weibo_cli_relevance_mode="llm"):
        assert get_llm_client() is runtime and get_llm_client() is runtime


def test_cli_sampling_signature_uses_relevance_backend_not_merge_backend(mixed, monkeypatch):
    from app.collectors.weibo import WeiboHeatCollector
    from app.schemas.collectors import CollectorRunConfig

    collector = WeiboHeatCollector(client=SimpleNamespace())
    config = CollectorRunConfig(source_id=collector.metadata.source_id)
    rule_signature = collector._result(config, status="succeeded").sampling_signature
    assert "reasoning_backend" not in rule_signature
    monkeypatch.setattr(settings, "weibo_cli_relevance_mode", "llm")
    llm_signature = collector._result(config, status="succeeded").sampling_signature
    assert "reasoning_backend" in llm_signature and rule_signature != llm_signature
