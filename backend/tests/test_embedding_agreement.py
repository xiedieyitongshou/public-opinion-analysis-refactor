"""Semantic gates across source pairs and the separate official support path."""

import pytest
from test_cli_semantic_merge import decide, pair
from test_day43_official_support import _event, _official_item

from app.agents.event_matcher import EventMatcher
from app.schemas import EventMatchConfig
from app.schemas.official_support import OfficialSupportConfig
from app.services.matching_engine import Comparison
from app.services.official_support import match_official_support


@pytest.mark.parametrize(
    "platforms", [["zhihu", "weibo"], ["weibo", "chinanews"], ["chinanews", "zhihu"]]
)
@pytest.mark.parametrize(
    "embedding,expected",
    [(0.337, "create"), (0.65, "candidate_review"), (0.75, "merge"), (None, "candidate_review")],
)
def test_source_pair_does_not_bypass_embedding_gate(platforms, embedding, expected):
    a, b = pair()
    a.platforms = platforms
    assert decide(a, b, score=0.999, embedding=embedding)[1][0] == expected


def test_specific_anchor_can_support_middle_band():
    a, b = pair()
    a.title = b.title = "问界M9碰撞交通灯后起火"
    assert decide(a, b, score=0.999, embedding=0.65)[1][0] == "merge"


def test_low_embedding_false_positive_does_not_outrank_valid_match(monkeypatch):
    a, b = pair()
    other = b.model_copy(update={"event_id": "unrelated", "title": "完全无关的另一条新闻"})
    monkeypatch.setattr(
        "app.agents.event_matcher.compare_many",
        lambda *args, **kw: [
            Comparison(rerank_score=0.999, embedding_similarity=0.35)
            if doc.title == other.title
            else Comparison(rerank_score=0.97, embedding_similarity=0.75)
            for doc in args[1]
        ],
    )
    result = EventMatcher().match_event(signal=a, candidates=[other, b], config=EventMatchConfig())
    assert result.action == "merge" and result.event_id == b.event_id


@pytest.mark.parametrize(
    "embedding,expected",
    [(0.4, "not_found"), (None, "not_found"), (0.65, "supported"), (0.8, "supported")],
)
def test_official_support_checks_embedding_even_with_title_anchor(monkeypatch, embedding, expected):
    monkeypatch.setattr(
        "app.services.official_support.compare_many",
        lambda *args, **kw: [
            Comparison(rerank_score=0.999, embedding_similarity=embedding, title_containment=True)
            for _ in args[1]
        ],
    )
    result = match_official_support(
        _event(),
        [_official_item(title=_event()["title"])],
        OfficialSupportConfig(use_embedding=True, use_reranker=True),
    )
    assert result.official_support_status == expected


def test_legacy_rules_only_does_not_require_model():
    result = match_official_support(_event(), [_official_item(title=_event()["title"])])
    assert result.official_support_status == "supported"


@pytest.mark.parametrize("embedding", [0.2, None])
def test_existing_identity_bypasses_semantic_gate(embedding):
    a, b = pair()
    b.event_signal_ids = [a.event_signal_id]
    assert decide(a, b, embedding=embedding)[1][0] == "merge"


@pytest.mark.parametrize("score,embedding", [(0.9664, 0.3369), (0.9981, 0.3971), (0.9884, 0.4545)])
def test_recorded_negative_metrics_stay_independent(score, embedding):
    a, b = pair()
    result, decision = decide(a, b, score=score, embedding=embedding)
    assert decision[0] == "create" and result.confidence == 0


@pytest.mark.parametrize("embedding", [0.7387, 0.7731, 0.7946])
def test_recorded_positive_metrics_retain_high_score_route(embedding):
    a, b = pair()
    assert decide(a, b, score=0.997, embedding=embedding)[1][0] == "merge"
