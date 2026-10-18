"""Low relevance rejects an association, not a valid independently observed hot topic."""
from datetime import timedelta

import pytest
from sqlalchemy import select
from test_day40_event_merge_guardrails import candidate, resolve, signal
from test_hotspot_analysis import START, environment, register_weibo_feed, request as base_request

from app.agents.analysis import HotspotAnalysisAgent
from app.models import Event, HumanReviewTask
from app.agents.event_matcher import EventMatcher
from app.schemas import EventMatchConfig, MatchFeatures, RerankEventMatchResult


@pytest.mark.parametrize("score, expected", [(0.099, "create"), (0.1, "candidate_review"), (None, "candidate_review")])
def test_raw_rejection_boundary_precedes_composite_review(score, expected):
    result = RerankEventMatchResult(
        candidate=candidate(), confidence=0.7,
        features=MatchFeatures(rerank_score=score), reason="composite review",
    )
    action, _, _ = EventMatcher()._decide(result, signal=seed(), config=EventMatchConfig())
    assert action == expected


def seed(title="快递员微信搭讪女业主被小区禁入", role="topic_discovery_signal"):
    return signal(title=title, keywords=[title], entities=[], action_terms=[],
                  event_time_hint=None, is_weak_signal=True).model_copy(update={
                      "source_roles": [role], "platforms": ["weibo"],
                      "event_text_for_match": title,
                  })


@pytest.mark.parametrize("score", [0.0, 0.023, 0.099])
def test_low_score_candidate_not_reintroduced_by_weak_guardrail(monkeypatch, score):
    class Model:
        def rerank(self, pairs):
            return [score] * len(pairs)

    monkeypatch.setattr("app.services.matching_engine.get_semantic_models", Model)
    incoming = seed()
    other = candidate(title="快递行业与网友自带菜被餐厅多收300元", entities=[], action_terms=[],
                      event_time_hint=None).model_copy(update={
                          "event_text_for_match": "网友自带菜被餐厅多收300元 快递",
                      })
    result = resolve(incoming, [other], extra={"match_config": {"use_reranker": True}})
    resolution = result.output["event_resolutions"][0]
    assert resolution["action"] == "create"
    assert resolution["review_required"] is False
    assert resolution["matched_candidate_event_id"] is None
    assert resolution["candidate_review"] is None
    assert resolution["match_features_json"]["rerank_score"] == score


def test_weak_hotlist_is_preserved_but_search_only_is_not_exempt():
    for role, review in [("topic_discovery_signal", False), ("search_enrichment_signal", True)]:
        resolution = resolve(seed(role=role), []).output["event_resolutions"][0]
        assert resolution["review_required"] is review


def test_weak_exact_identity_still_merges():
    incoming = seed()
    existing = candidate(title=incoming.title, entities=[], action_terms=[],
                         event_time_hint=None, source_signal_ids=incoming.source_signal_ids)
    resolution = resolve(incoming, [existing]).output["event_resolutions"][0]
    assert resolution["action"] == "merge"
    assert resolution["review_required"] is False


def test_same_title_without_identity_keeps_weak_merge_review():
    incoming = seed()
    existing = candidate(title=incoming.title, keywords=incoming.keywords,
                         entities=[], action_terms=[], event_time_hint=None)
    resolution = resolve(incoming, [existing]).output["event_resolutions"][0]
    assert resolution["review_required"] is True


def test_rss_titles_without_entities_reach_classification_and_reuse_identity(environment, monkeypatch):
    db, clock, _ = environment
    titles = ["肯德基将换百事可乐为可口可乐", "快递员微信搭讪女业主被小区禁入", "MAMA阵容"]
    register_weibo_feed(environment, monkeypatch, {"titles": titles})
    agent = HotspotAnalysisAgent()
    first = agent.run(db, request("title-fallback-1", START, sources=["weibo_rsshub_hot_search"]))
    assert len(first.event_ids) == len(titles)
    assert set(db.scalars(select(Event.title))) == set(titles)
    assert not list(db.scalars(select(HumanReviewTask)))
    assert all(c.classification.platform_presence.weibo_topn for c in first.classifications)
    assert all(c.classification.priority_category == "E_single_platform_only"
               for c in first.classifications)
    clock.time += timedelta(hours=2)
    second = agent.run(db, request("title-fallback-2", clock.time,
                                   sources=["weibo_rsshub_hot_search"]))
    assert set(second.event_ids) == set(first.event_ids)
    assert not list(db.scalars(select(HumanReviewTask)))


def request(*args, **kwargs):
    value = base_request(*args, **kwargs)
    value.collection.limit = 3
    return value
