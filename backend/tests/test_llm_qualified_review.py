"""Qualified uncertainty must neither block sound matches nor bypass evidence checks."""

import pytest
from test_llm_reasoning import (
    TITLE,
    candidate,
    same_pair,
    signal,
)
from test_llm_reasoning import (
    llm as llm,
)

from app.agents.event_matcher import EventMatcher
from app.schemas import EventMatchConfig
from app.schemas.llm import LLMPairDecision
from app.services.llm_event_workflow import comparison_groups


def context_only(candidate_id="event-2"):
    return {
        "candidate_id": candidate_id, "decision": "insufficient_evidence",
        "evidence": [], "same_event_clues": [],
        "uncertainty_kind": "insufficient_context", "conflicts": [],
        "missing_information": ["只有短标题，没有具体事件上下文"],
        "reason": "缺少上下文，也没有双方指向同一具体事情的线索",
    }


def match(candidates):
    return EventMatcher().match_event(signal=signal(), candidates=candidates,
                                     config=EventMatchConfig())


def test_uninformative_candidate_does_not_block_one_clear_match(llm):
    llm[0]["output"] = {"judgements": [same_pair(), context_only()]}
    result = match([candidate(), candidate("event-2")])
    assert result.action == "merge" and not result.review_required
    assert result.event_id == "event-1"
    detail = result.match_features_json.llm_detail
    assert detail["candidate_groups"]["context_only"] == ["event-2"]
    # Unknown remains unknown in the audit, not silently relabelled as a different event.
    assert detail["comparisons"][1]["judgement"]["decision"] == "insufficient_evidence"


def test_context_only_without_match_keeps_independent_topic(llm):
    llm[0]["output"] = {"judgements": [context_only("event-1")]}
    result = match([candidate()])
    assert result.action == "create" and not result.review_required
    assert result.matched_candidate_event_id is None
    assert result.match_features_json.llm_detail["review_category"] == "independent_topic"


def test_distinct_titles_need_no_quotes_and_keep_flight_promotion_independent(llm):
    from app.schemas import EventSignal

    incoming = EventSignal(event_signal_id="promotion", source_signal_ids=["saved-hotlist-topic"],
                           title="上飞猪抢马来西亚酒店大额补贴",
                           event_text_for_match="上飞猪抢马来西亚酒店大额补贴",
                           is_weak_signal=True, weak_signal_reason="title_only", confidence=0.65)
    target = candidate().model_copy(update={"title": "世界互联网大会乌镇峰会创下多个首次"})
    llm[0]["output"] = {"judgements": [{
        "candidate_id": "event-1", "decision": "different_event", "evidence": [],
        "conflicts": [], "missing_information": [], "reason": "酒店补贴与会议报道是不同事情",
        "uncertainty_kind": None, "same_event_clues": [],
    }]}
    result = EventMatcher().match_event(signal=incoming, candidates=[target],
                                        config=EventMatchConfig())
    assert result.action == "create" and not result.review_required
    assert result.matched_candidate_event_id is None


def test_optional_negative_quote_is_still_validated_when_model_supplies_it(llm):
    row = same_pair(decision="different_event")
    row["evidence"][1]["quote"] = "2026年" + TITLE
    llm[0]["output"] = {"judgements": [row]}
    result = match([candidate()])
    assert result.action == "candidate_review"
    assert result.match_features_json.llm_detail["error_code"] == "llm_unverifiable_evidence"


def test_same_event_still_requires_both_source_quotes(llm):
    row = same_pair()
    row["evidence"] = []
    llm[0]["output"] = {"judgements": [row]}
    result = match([candidate()])
    assert result.action == "candidate_review"
    assert result.match_features_json.llm_detail["error_code"] == "llm_missing_two_sided_evidence"


@pytest.mark.parametrize("with_clear_match", [True, False])
def test_specific_clues_with_missing_detail_still_require_review(llm, with_clear_match):
    uncertain = same_pair("event-2", "insufficient_evidence")
    llm[0]["output"] = {"judgements": ([same_pair()] if with_clear_match else []) + [uncertain]}
    result = match(([candidate()] if with_clear_match else []) + [candidate("event-2")])
    assert result.action == "candidate_review" and result.review_required
    assert result.match_features_json.llm_detail["review_category"] == "plausible_uncertainty"


def test_two_clear_matches_are_still_exclusive_and_reviewed(llm):
    llm[0]["output"] = {"judgements": [same_pair(), same_pair("event-2")]}
    result = match([candidate(), candidate("event-2")])
    assert result.action == "candidate_review" and result.review_required
    assert result.match_features_json.llm_detail["review_category"] == "competing_matches"


@pytest.mark.parametrize("invalid", ["missing_kind", "one_sided_clue", "false_clue",
                                     "no_missing_detail", "context_with_clues"])
def test_bad_uncertainty_evidence_becomes_technical_review_not_auto_merge(llm, invalid):
    row = same_pair("event-2", "insufficient_evidence")
    if invalid == "missing_kind":
        del row["uncertainty_kind"]
    elif invalid == "one_sided_clue":
        row["same_event_clues"] = row["same_event_clues"][:1]
    elif invalid == "false_clue":
        row["same_event_clues"][0] = {"source_id": "incoming", "quote": "不存在的证据"}
    elif invalid == "no_missing_detail":
        row["missing_information"] = [" "]
    else:
        row["uncertainty_kind"] = "insufficient_context"
    llm[0]["output"] = {"judgements": [same_pair(), row]}
    result = match([candidate(), candidate("event-2")])
    assert result.action == "candidate_review" and result.blocks_auto_analysis
    assert result.match_features_json.llm_detail["review_category"] == "technical_error"
    assert result.matched_candidate_event_id is None


def test_invalid_clear_match_is_not_hidden_by_uninformative_candidate(llm):
    bad = same_pair()
    bad["evidence"][0]["quote"] = "不存在的发布会"
    llm[0]["output"] = {"judgements": [bad, context_only()]}
    result = match([candidate(), candidate("event-2")])
    assert result.action == "candidate_review"
    assert result.match_features_json.llm_detail["error_code"] == "llm_unverifiable_evidence"


def test_legacy_uncertainty_is_not_reinterpreted_as_harmless(llm, monkeypatch):
    legacy = context_only()
    del legacy["uncertainty_kind"]
    del legacy["same_event_clues"]
    decisions = [(LLMPairDecision.model_validate(x), {}) for x in [same_pair(), legacy]]
    assert comparison_groups(decisions)["legacy"]
    monkeypatch.setattr("app.services.llm_event_workflow.judge_pairs", lambda *a, **k: decisions)
    result = match([candidate(), candidate("event-2")])
    assert result.review_required and result.action == "candidate_review"
    assert result.match_features_json.llm_detail["review_category"] == "legacy_uncertainty"


def test_missing_candidate_output_still_fails_coverage_check(llm):
    llm[0]["output"] = {"judgements": [same_pair()]}
    result = match([candidate(), candidate("event-2")])
    assert result.review_required
    assert result.match_features_json.llm_detail["error_code"] == "llm_candidate_set_mismatch"


def test_quoted_title_is_valid_event_clue_when_explicitly_judged_possible(llm):
    row = same_pair("event-1", "insufficient_evidence")
    assert row["same_event_clues"][0]["quote"] == TITLE
    llm[0]["output"] = {"judgements": [row]}
    result = match([candidate()])
    assert result.review_required
    assert result.match_features_json.llm_detail["candidate_groups"]["plausible"] == ["event-1"]
