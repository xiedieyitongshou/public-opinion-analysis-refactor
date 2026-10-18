from test_day39_event_matcher import candidate, event_signal
from test_overnight_test import db as db

from app.agents.event_matcher import EventMatcher
from app.schemas import EventMatchConfig
from app.services.event_constraints import title_conflicts
from app.services.matching_engine import Comparison


def compare(left, right, score=0.9999, conflicts=None):
    signal = event_signal(title=left, entities=[], action_terms=[], event_time_hint=None)
    signal.event_text_for_match = left
    signal.keywords = ["生活费", "4000", "2000"]
    target = candidate(title=right, entities=[], action_terms=[], event_time_hint=None)
    target.event_text_for_match = right
    target.keywords = signal.keywords
    config = EventMatchConfig()
    result = EventMatcher().rerank_candidate(
        signal=signal,
        candidate=target,
        config=config,
        bm25_raw_score=4,
        comparison=Comparison(
            rerank_score=score,
            embedding_similarity=0.9,
            conflicts=conflicts or [],
            missing=["entities", "action_terms", "event_time"],
        ),
    )
    return signal, result, EventMatcher()._decide(result, signal=signal, config=config)


def test_high_semantic_agreement_does_not_require_ner():
    signal, result, decision = compare(
        "母亲将大一儿子生活费从4000降至2000", "妈妈将大一儿子生活费从4000元降至2000元"
    )
    assert result.features.semantic_event_support
    assert decision[0] == "merge" and not decision[2]
    from app.guardrails import default_guardrail_registry
    from app.guardrails.event_merge import apply_merge_guardrails
    from app.schemas import EventResolution

    resolution = EventResolution(
        event_signal_id=signal.event_signal_id,
        event_id="target",
        event_fingerprint="test",
        action="merge",
        confidence=result.confidence,
        reason="semantic agreement",
        match_features_json=result.features,
        matched_candidate_event_id="target",
    )
    guarded = apply_merge_guardrails(
        resolution, event_signal=signal.model_dump(mode="json"), registry=default_guardrail_registry
    )
    assert guarded.action == "merge" and not guarded.review_required


def test_explicit_conflict_overrides_near_one_score():
    _, result, decision = compare(
        "某市发布第1196期产品召回通知",
        "某市发布第1197期产品召回通知",
        conflicts=["episode_conflict"],
    )
    assert not result.features.semantic_event_support and decision[0] == "reject"
    assert "episode_conflict" in title_conflicts("海贼王第1196话", "海贼王第1197话")


def test_generic_and_medium_score_do_not_auto_merge():
    assert compare("重阳节", "重阳节有哪些诗词值得一读")[2][0] != "merge"
    assert (
        compare(
            "母亲将大一儿子生活费从4000降至2000",
            "妈妈将大一儿子生活费从4000元降至2000元",
            score=0.7,
        )[2][0]
        != "merge"
    )


def test_real_long_cross_platform_title_with_matching_amounts():
    assert (
        compare(
            "妈妈因大一儿子未考技能证书，将生活费从4000元降至2000，够花吗？怎样看待这种「降费逼考证」方式？",
            "母亲将大一儿子生活费从4000降至2000",
        )[2][0]
        == "merge"
    )


def test_pending_relation_key_ignores_run_but_not_new_conflict():
    from app.services.human_review import _idempotency_key

    a = dict(event_signal_id="s", candidate_event_id="e", run_id="a")
    b = {**a, "run_id": "b"}
    assert _idempotency_key("event_merge_candidate", a) == _idempotency_key(
        "event_merge_candidate", b
    )
    b["match_features_json"] = {"guardrail_flags": ["episode_conflict"]}
    assert _idempotency_key("event_merge_candidate", a) != _idempotency_key(
        "event_merge_candidate", b
    )


def test_reviewed_rejection_is_reused_but_new_conflict_is_not_overridden(db):
    from app.models import HumanReviewTask
    from app.schemas import EventResolution
    from app.services.human_review import reuse_reviewed_merge

    signal = event_signal()
    db.add(
        HumanReviewTask(
            review_type="event_merge_candidate",
            status="resolved",
            decision="reject",
            payload_json={
                "event_signal_id": signal.event_signal_id,
                "candidate_event_id": "target",
            },
        )
    )
    db.commit()
    resolution = EventResolution(
        event_signal_id=signal.event_signal_id,
        event_id="target",
        event_fingerprint="test",
        action="merge",
        confidence=0.99,
        reason="new score",
    )
    assert reuse_reviewed_merge(db, signal, resolution).action == "reject"
    resolution.action = "candidate_review"
    resolution.match_features_json.guardrail_flags = ["cluster_conflict"]
    assert reuse_reviewed_merge(db, signal, resolution).action == "candidate_review"


def test_multiple_similarly_strong_candidates_still_require_review(monkeypatch):
    import app.agents.event_matcher as module

    title = "母亲将大一儿子生活费从4000降至2000"
    signal = event_signal(title=title, entities=[], action_terms=[], event_time_hint=None)
    choices = [
        candidate(title=title, event_id="a", entities=[], action_terms=[], event_time_hint=None),
        candidate(title=title, event_id="b", entities=[], action_terms=[], event_time_hint=None),
    ]
    monkeypatch.setattr(
        module,
        "compare_many",
        lambda *args, **kwargs: [
            Comparison(rerank_score=0.999, title_containment=True),
            Comparison(rerank_score=0.998, title_containment=True),
        ],
    )
    result = EventMatcher().match_event(
        signal=signal, candidates=choices, config=EventMatchConfig()
    )
    assert result.action == "candidate_review"
    assert result.reason == "multiple similarly strong event candidates"
