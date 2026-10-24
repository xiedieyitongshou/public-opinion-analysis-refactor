"""High reranker confidence may replace missing NER, not contradictory facts."""

import pytest
from test_day39_event_matcher import candidate, event_signal

from app.agents.event_matcher import EventMatcher
from app.schemas import EventMatchConfig
from app.services.matching_engine import Comparison


def pair():
    a = event_signal(
        title="问界回应", entities=[], action_terms=[], event_time_hint=None, is_weak_signal=True
    )
    a.summary = "问界M9碰撞交通灯后起火，警方正在调查事故原因。"
    a.has_accepted_cli_context = True
    b = candidate(
        title="一辆问界M9撞击交通灯后起火，警方正在调查",
        entities=[],
        action_terms=[],
        event_time_hint=None,
    )
    return a, b


def decide(a, b, score=0.96, flags=None, embedding=0.75):
    matcher = EventMatcher()
    result = matcher.rerank_candidate(
        signal=a,
        candidate=b,
        config=EventMatchConfig(),
        comparison=Comparison(
            rerank_score=score, embedding_similarity=embedding, quality_flags=flags or []
        ),
    )
    return result, matcher._decide(result, signal=a, config=EventMatchConfig())


@pytest.mark.parametrize("reverse", [False, True])
def test_cli_body_on_either_side_relaxes_short_title_and_missing_entities(reverse):
    a, b = pair()
    if reverse:
        a.title, b.title = b.title, a.title
        a.summary, b.summary = b.summary, a.summary
        a.has_accepted_cli_context, b.has_accepted_cli_context = False, True
    result, decision = decide(a, b)
    assert result.features.semantic_event_support
    assert decision[0] == "merge"


def test_untyped_disjoint_entities_are_not_contradiction():
    a, b = pair()
    a.entities, b.entities = ["问界"], ["警方"]
    assert decide(a, b)[1][0] == "merge"


@pytest.mark.parametrize("change", ["untrusted", "empty", "short_target", "low", "unavailable"])
def test_insufficient_context_or_score_does_not_use_high_score_route(change):
    a, b = pair()
    if change == "untrusted":
        a.has_accepted_cli_context = False
    if change == "empty":
        a.summary = ""
    if change == "short_target":
        b.title = "事故"
    result, decision = decide(
        a,
        b,
        score=0.949 if change == "low" else 0.99,
        flags=["model_unavailable"] if change == "unavailable" else [],
    )
    assert not result.features.semantic_event_support
    assert decision[0] != "merge"


def test_explicit_body_model_conflict_still_blocks():
    a, b = pair()
    b.title = "问界M7碰撞交通灯后起火"
    result, decision = decide(a, b, score=0.999)
    assert "model_conflict" in result.features.guardrail_flags
    assert decision[0] == "reject"


def test_two_high_scoring_candidates_still_require_review(monkeypatch):
    a, b = pair()
    other = b.model_copy(update={"event_id": "other"})
    monkeypatch.setattr(
        "app.agents.event_matcher.compare_many",
        lambda *args, **kwargs: [
            Comparison(rerank_score=0.99, embedding_similarity=0.75) for _ in args[1]
        ],
    )
    result = EventMatcher().match_event(signal=a, candidates=[b, other], config=EventMatchConfig())
    assert result.action == "candidate_review"
    assert result.reason == "multiple similarly strong event candidates"


@pytest.mark.parametrize(
    "embedding,action",
    [
        (0.337, "create"),
        (0.599, "create"),
        (0.60, "candidate_review"),
        (0.693, "candidate_review"),
        (0.70, "merge"),
        (None, "candidate_review"),
    ],
)
def test_embedding_gates_high_reranker(embedding, action):
    a, b = pair()
    _, decision = decide(a, b, score=0.999, embedding=embedding)
    assert decision[0] == action
