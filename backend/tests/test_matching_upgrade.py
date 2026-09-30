"""Regression contracts and retrieval/model boundary checks, without downloads."""

from datetime import UTC, datetime

import pytest

from app.agents.event_matcher import EventMatcher
from app.schemas.matching import (
    EventMatchConfig,
    RetrievedEventMatchCandidate,
    SourceSignalMatchRef,
)
from app.schemas.signals import EventSignal
from app.services.match_documents import MatchDocument, canonical_ids, event_document
from app.services.matching_engine import bm25_scores, compare_many, retrieve
from app.services.official_support import match_official_support
from app.services.semantic_models import LocalSemanticModels, SemanticUnavailable


def test_nested_features_are_authoritative_and_legacy_rows_remain_readable():
    event = {
        "title": "医保改革",
        "primary_entity": "旧机构",
        "event_detail": {
            "entities": ["旧机构"],
            "action_terms": ["发布"],
            "match_features": {"entities": [], "action_terms": []},
        },
    }
    document = event_document(event)
    assert document.entities == document.action_terms == []
    del event["event_detail"]["match_features"]
    assert event_document(event).entities == ["旧机构"]
    assert event_document(event).action_terms == ["发布"]


def test_observation_time_is_not_event_time():
    document = event_document({"title": "旧事件回顾", "first_seen_at": datetime.now(UTC)})
    assert document.event_time is None
    assert document.observed_at is not None


def test_zhihu_parent_identity_keeps_namespace_and_rejects_lookalike_host():
    assert canonical_ids(
        [
            "https://www.zhihu.com/question/123",
            "https://www.zhihu.com/question/123/answer/456",
        ]
    ) == ["zhihu:question:123"]
    assert canonical_ids(["https://zhihu.com.evil.test/question/123"]) == []


def test_empty_action_does_not_match_candidates_own_action():
    event = {
        "title": "老人拾荒补缴养老金",
        "event_detail": {"match_features": {"entities": [], "action_terms": []}},
    }
    result = match_official_support(
        event,
        [
            {
                "title": "中国高校赴安哥拉开展讲座",
                "source_type": "official_news",
                "source_status": "use",
                "signal_role": "evidence_signal",
                "url": "https://www.chinanews.com.cn/test",
                "published_at": datetime.now(UTC),
            }
        ],
    )
    assert result.official_support_status == "not_found"
    assert result.official_references == []


def test_bm25_uses_corpus_idf_and_document_length():
    scores = bm25_scores("rare common", ["rare", "common", "common common", "common"])
    assert scores[0] > scores[1]
    short, long = bm25_scores("target", ["target", "target " + "unrelated " * 50])
    assert short > long > 0


def test_dense_candidates_survive_zero_lexical_overlap(monkeypatch):
    class Vector:
        def __init__(self, number):
            self.number = number

        def __matmul__(self, other):
            return other.number

    class Models:
        def encode(self, texts):
            return [Vector(n) for n in (1, 0.1, 0.95)]

    monkeypatch.setattr("app.services.matching_engine.get_semantic_models", lambda: Models())
    indices, audit = retrieve(
        MatchDocument(title="飞机迫降"),
        [
            MatchDocument(title="飞机制造"),
            MatchDocument(title="航班紧急落地"),
        ],
        use_embedding=True,
        limit=2,
    )
    assert 1 in indices
    assert 1 not in audit["sparse_indices"]
    assert audit["dense_indices"][0] == 1


def test_missing_models_return_unknown_and_visible_degradation(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.matching_engine.get_semantic_models", lambda: LocalSemanticModels(tmp_path)
    )
    result = compare_many(
        MatchDocument(title="甲事件"),
        [MatchDocument(title="甲事件")],
        use_embedding=True,
        use_reranker=True,
    )[0]
    assert result.embedding_similarity is None
    assert result.rerank_score is None
    assert "embedding_model_not_prepared" in result.quality_flags
    assert "reranker_model_not_prepared" in result.quality_flags
    with pytest.raises(SemanticUnavailable):
        LocalSemanticModels(tmp_path).encode(["text"])


def test_disabled_semantics_never_loads_models(monkeypatch):
    class FailModels:
        def encode(self, texts):
            raise AssertionError("model unexpectedly loaded")

        def rerank(self, pairs):
            raise AssertionError("model unexpectedly loaded")

    monkeypatch.setattr("app.services.matching_engine.get_semantic_models", lambda: FailModels())
    assert (
        compare_many(MatchDocument(title="甲"), [MatchDocument(title="乙")])[0].rerank_score is None
    )


def test_high_reranker_score_cannot_override_explicit_date_conflict(monkeypatch):
    class Model:
        def rerank(self, pairs):
            return [0.99] * len(pairs)

    monkeypatch.setattr("app.services.matching_engine.get_semantic_models", lambda: Model())
    target = MatchDocument(title="2026年9月1日甲地发生地震")
    other = MatchDocument(title="2026年9月9日甲地发生地震")
    comparison = compare_many(target, [other], use_reranker=True)[0]
    assert comparison.rerank_score == 0.99
    assert comparison.conflicts == ["explicit_date_conflict"]


def test_parent_question_identity_merges_even_when_answer_title_differs(monkeypatch):
    def unexpected():
        raise AssertionError("Identity matching must not need semantic weights")

    monkeypatch.setattr("app.services.matching_engine.get_semantic_models", unexpected)
    signal = EventSignal(
        event_signal_id="answer",
        source_signal_ids=["s"],
        title="养老金后续答复",
        event_text_for_match="养老金后续答复",
        semantic_fingerprint={},
        confidence=0.9,
    )
    result = EventMatcher().match_event(
        signal=signal,
        candidates=[
            RetrievedEventMatchCandidate(
                event_id="question-event",
                title="老人拾荒补缴养老保险",
                source_urls=["https://www.zhihu.com/question/123"],
            )
        ],
        config=EventMatchConfig(use_embedding=True, use_reranker=True),
        source_refs_by_signal_id={
            "s": SourceSignalMatchRef(
                source_signal_id="s", url="https://www.zhihu.com/question/123/answer/456"
            )
        },
    )
    assert result.action == "merge"
    assert result.event_id == "question-event"
    assert "canonical_id" in result.matched_by
    assert result.match_features_json.hard_constraints_passed
    assert (
        "embedding_only_without_hard_constraint" not in result.match_features_json.guardrail_flags
    )


def test_equivalent_calendar_dates_do_not_conflict():
    comparison = compare_many(
        MatchDocument(title="2026年9月1日召开会议"), [MatchDocument(title="2026-09-01召开会议")]
    )[0]
    assert not comparison.conflicts


def test_related_official_title_without_specific_anchor_is_not_evidence(monkeypatch):
    class Model:
        def rerank(self, pairs):
            return [0.9999] * len(pairs)

    monkeypatch.setattr("app.services.matching_engine.get_semantic_models", lambda: Model())
    from app.schemas.official_support import OfficialSupportConfig

    result = match_official_support(
        {
            "title": "星航制药新型流感疫苗获批上市",
            "keywords": ["星航制药", "流感疫苗", "获批上市"],
            "event_detail": {"match_features": {"entities": [], "action_terms": []}},
        },
        [
            {
                "title": "星航制药新型新冠疫苗获批上市",
                "source_type": "official_news",
                "source_status": "use",
                "signal_role": "evidence_signal",
                "url": "https://example.test/news/2",
                "published_at": datetime.now(UTC),
            }
        ],
        OfficialSupportConfig(use_reranker=True),
    )
    assert result.official_support_status == "not_found"
    assert not result.official_references


def test_model_failure_is_propagated_to_match_tool_status(monkeypatch, tmp_path):
    from app.schemas.matching import MatchAndResolveEventsInput

    monkeypatch.setattr(
        "app.services.matching_engine.get_semantic_models", lambda: LocalSemanticModels(tmp_path)
    )
    signal = EventSignal(
        event_signal_id="new",
        title="城市供水故障",
        source_signal_ids=["new"],
        event_text_for_match="城市供水故障",
        semantic_fingerprint={},
        confidence=0.9,
    )
    result = EventMatcher().resolve(
        MatchAndResolveEventsInput(
            run_id="missing-model",
            event_signals=[signal],
            existing_events=[RetrievedEventMatchCandidate(event_id="old", title="城市供水维修")],
            match_config=EventMatchConfig(use_embedding=True, use_reranker=True),
        )
    )
    assert result.status == "partial"
    assert "embedding_model_not_prepared" in result.quality_flags
    assert "reranker_model_not_prepared" in result.quality_flags


@pytest.mark.parametrize(
    "left,right,flag",
    [
        ("启明发布K9", "启明发布K8", "model_conflict"),
        ("启明召回2024款K9", "启明发布2025款K9", "model_year_conflict"),
        ("甲城市道路塌陷", "乙城市道路塌陷", "location_conflict"),
        ("甲大学取消周末课程", "甲大学增设周末课程", "action_conflict"),
    ],
)
def test_explicit_facts_block_both_decisions_even_with_high_reranker(
    monkeypatch,
    left,
    right,
    flag,
):
    class Model:
        def rerank(self, pairs):
            return [0.9999] * len(pairs)

    monkeypatch.setattr("app.services.matching_engine.get_semantic_models", lambda: Model())
    signal = EventSignal(
        event_signal_id="left",
        source_signal_ids=["s"],
        title=left,
        event_text_for_match=left,
        semantic_fingerprint={},
        confidence=0.9,
    )
    resolution = EventMatcher().match_event(
        signal=signal,
        candidates=[RetrievedEventMatchCandidate(event_id="right", title=right)],
        config=EventMatchConfig(use_reranker=True),
    )
    assert resolution.action != "merge"
    assert flag in resolution.match_features_json.guardrail_flags
    from app.schemas.official_support import OfficialSupportConfig

    support = match_official_support(
        {"title": left},
        [
            {
                "title": right,
                "source_type": "official_news",
                "source_status": "use",
                "signal_role": "evidence_signal",
                "url": "https://example.test/news/1",
                "published_at": datetime.now(UTC),
            }
        ],
        OfficialSupportConfig(use_reranker=True),
    )
    assert support.official_support_status == "not_found"


def test_representative_conflict_requires_review():
    signal = EventSignal(
        event_signal_id="new",
        source_signal_ids=["s"],
        title="同一事故进展",
        event_text_for_match="同一事故进展",
        event_time_hint="2026-09-29T00:00:00Z",
        semantic_fingerprint={},
        confidence=0.9,
    )
    candidate = RetrievedEventMatchCandidate(
        event_id="old",
        title="同一事故进展",
        event_time_hint=signal.event_time_hint,
        representative_documents=[
            MatchDocument(
                title="同一事故进展", event_time=datetime(2026, 1, 1, tzinfo=UTC)
            ).model_dump(mode="json")
        ],
    )
    result = EventMatcher().match_event(
        signal=signal, candidates=[candidate], config=EventMatchConfig()
    )
    assert result.action == "candidate_review"
    assert "cluster_conflict" in result.match_features_json.guardrail_flags
