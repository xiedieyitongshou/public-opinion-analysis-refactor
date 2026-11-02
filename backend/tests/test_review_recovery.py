"""Successful later decisions retire technical failures, never genuine ambiguity."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from test_day41_human_review_queue import make_session
from test_hotspot_analysis import START, TITLE, HotspotAnalysisAgent, request
from test_hotspot_analysis import environment as environment
from test_review_batch import request as batch_request
from test_review_batch import seed

from app.models import AgentTask, Event, HumanReviewTask
from app.models.briefing import AnalysisRun
from app.schemas import EventResolution, HumanReviewDecisionInput
from app.services.human_review import _decide_human_review_task, task_record
from app.services.review_batch import batch_merge_reviews
from app.services.review_recovery import recover_saved_technical_reviews, recover_technical_reviews

NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def technical(task, **changes):
    task.payload_json = {
        **task.payload_json, "candidate_event_id": None,
        "match_features_json": {
            "reasoning_mode": "llm", "semantic_quality_flags": ["llm_unverifiable_evidence"],
            "llm_detail": {"review_category": "technical_error",
                           "error_code": "llm_unverifiable_evidence"},
        }, **changes,
    }


def accepted(db, item, *, hours=1, **changes):
    event = db.scalar(select(Event).where(Event.event_id == "a"))
    item.event_id = event.id
    args = {
        "resolution": EventResolution(event_signal_id="success", event_id="a",
                                      event_fingerprint="test", action="create",
                                      confidence=1, reason="Validated independent topic"),
        "sources": [{"item_id": str(item.id), "source_signal_id": "new-source",
                     "url": item.url, "title": item.title,
                     "source_type": item.source.source_type}],
        "run_id": "new-round", "observed_at": NOW + timedelta(hours=hours),
    }
    return {**args, **changes}


@pytest.mark.parametrize("action", ["create", "merge"])
@pytest.mark.parametrize("mode", ["llm", "rules"])
def test_later_valid_decision_closes_technical_review_and_preserves_audit(action, mode):
    with make_session() as db:
        task, item = seed(db)
        technical(task)
        db.commit()
        original = deepcopy(task.payload_json)
        args = accepted(db, item)
        args["resolution"].action = action
        args["resolution"].match_features_json.reasoning_mode = mode
        assert [a["review_id"] for a in recover_technical_reviews(db, **args)] == [task.id]
        db.commit()
        assert task.status == "cancelled" and task.decision == "auto_recovered"
        assert task_record(task).decision == "auto_recovered"
        assert task.reviewer == "system:later-success"
        assert {k: task.payload_json[k] for k in original} == original
        assert task.payload_json["automatic_recovery"]["action"] == action
        assert recover_technical_reviews(db, **args) == []
        assert _decide_human_review_task(
            db, task, HumanReviewDecisionInput(decision="ignore")
        ).message == "该技术异常已被后续成功结果处理，无需重复审核。"


@pytest.mark.parametrize("category", ["plausible_uncertainty", "competing_matches",
                                      "legacy_uncertainty", "rules"])
def test_existing_owner_does_not_clear_semantic_reviews(category):
    with make_session() as db:
        task, item = seed(db)
        technical(task)
        features = task.payload_json["match_features_json"]
        features["llm_detail"]["review_category"] = category
        assert not recover_technical_reviews(db, **accepted(db, item))
        assert task.status == "pending"


@pytest.mark.parametrize("kind", ["conflict", "history_conflict", "history_semantic",
                                  "resolved", "human_started", "other_review_type"])
def test_conflicts_history_and_human_decisions_are_preserved(kind):
    with make_session() as db:
        task, item = seed(db)
        technical(task)
        if kind == "conflict":
            task.payload_json["guardrail_flags"] = ["time_conflict"]
        elif kind.startswith("history"):
            features = ({"guardrail_flags": ["entity_conflict"]}
                        if kind == "history_conflict" else
                        {"llm_detail": {"review_category": "plausible_uncertainty"}})
            task.payload_json["evidence_history"] = [{"match_features_json": features}]
        elif kind == "resolved":
            task.status = "resolved"
        elif kind == "human_started":
            task.reviewer = "person"
        else:
            task.review_type = "publish_review"
        assert not recover_technical_reviews(db, **accepted(db, item))
        assert task.status == ("resolved" if kind == "resolved" else "pending")


@pytest.mark.parametrize("change", [{"action": "candidate_review"}, {"review_required": True},
                                   {"blocks_auto_analysis": True}, {"guardrail_status": "block"}])
def test_unaccepted_result_cannot_retire_error_even_with_existing_owner(change):
    with make_session() as db:
        task, item = seed(db)
        technical(task)
        args = accepted(db, item)
        args["resolution"] = args["resolution"].model_copy(update=change)
        assert not recover_technical_reviews(db, **args)
        assert task.status == "pending"


def test_strong_question_identity_covers_new_snapshot_without_reassigning_old_item():
    with make_session() as db:
        task, old_item = seed(db)
        technical(task)
        _, new_item = seed(db, offset=1)
        db.commit()
        assert recover_technical_reviews(db, **accepted(db, new_item))
        assert task.status == "cancelled" and old_item.event_id is None


@pytest.mark.parametrize("case", ["other_question", "owner_conflict", "same_generic_title",
                                  "missing_item", "unpersisted_owner", "inactive_event"])
def test_identity_and_current_ownership_are_verified(case):
    with make_session() as db:
        task, old_item = seed(db)
        technical(task)
        _, new_item = seed(db, offset=1, question=999 if case == "other_question" else 123)
        if case == "same_generic_title":
            task.payload_json["source_citations"] = [{"title": "same", "url": "https://x.test/1"}]
            old_item.url, new_item.url = "https://x.test/1", "https://x.test/2"
            new_item.title = "same"
        elif case == "owner_conflict":
            other = Event(event_id="b", title="other")
            db.add(other)
            db.flush()
            old_item.event_id = other.id
        elif case == "missing_item":
            task.payload_json["item_ids"] = ["999999"]
        args = accepted(db, new_item)
        if case == "unpersisted_owner":
            new_item.event_id = None
        elif case == "inactive_event":
            db.scalar(select(Event).where(Event.event_id == "a")).lifecycle_status = "merged"
            db.flush()
        assert not recover_technical_reviews(db, **args)
        assert task.status == "pending"


@pytest.mark.parametrize("hours", [-1, 1])
def test_latest_failure_round_controls_ordering(hours):
    with make_session() as db:
        task, item = seed(db)
        technical(task, run_ids=["early", "late"])
        for key, offset in (("early", 0), ("late", 2)):
            db.add(AnalysisRun(run_id=key, status="partial", started_at=NOW,
                               window_end=NOW + timedelta(hours=offset), input_json={}))
        db.commit()
        assert not recover_technical_reviews(db, **accepted(db, item, hours=hours))
        assert task.status == "pending"


def test_weibo_identity_window_uses_capture_date_and_first_failed_round():
    with make_session() as db:
        task, item = seed(db, weibo=True)
        technical(task, run_ids=["early", "late"])
        for key, hours in (("early", 0), ("late", 23)):
            db.add(AnalysisRun(run_id=key, status="partial", started_at=datetime.now(UTC),
                               window_end=NOW + timedelta(hours=hours), input_json={}))
        db.commit()
        assert not recover_technical_reviews(db, **accepted(db, item, hours=25))
        assert task.status == "pending"
        assert recover_technical_reviews(db, **accepted(db, item, hours=24))


def test_missing_round_record_does_not_hide_newer_frozen_failure_observation():
    with make_session() as db:
        task, item = seed(db)
        technical(task, run_ids=["early", "missing-late"], identity_sources=[{
            "url": item.url, "fetched_at": (NOW + timedelta(hours=2)).isoformat(),
        }])
        db.add(AnalysisRun(run_id="early", status="partial", started_at=NOW,
                           window_end=NOW, input_json={}))
        db.commit()
        assert not recover_technical_reviews(db, **accepted(db, item, hours=1))


def test_same_window_requires_proof_of_later_attempt_not_second_precision_timestamp():
    with make_session() as db:
        task, item = seed(db)
        technical(task)
        tasks = [AgentTask(task_type="match_and_resolve_events", status="succeeded")
                 for _ in range(3)]
        db.add_all(tasks)
        db.flush()
        task.agent_task_id = tasks[1].id
        db.commit()
        args = accepted(db, item, hours=0, agent_task_id=tasks[0].id)
        assert not recover_technical_reviews(db, **args)
        args["agent_task_id"] = tasks[2].id
        assert recover_technical_reviews(db, **args)


def test_backfill_dry_run_then_apply_and_repeat_are_safe():
    with make_session() as db:
        task, item = seed(db)
        technical(task)
        args = accepted(db, item)
        resolution = args["resolution"].model_dump(mode="json")
        db.add(AgentTask(
            task_type="match_and_resolve_events", status="succeeded",
            finished_at=datetime.now(UTC), input_json={
                "run_id": args["run_id"], "window_end": args["observed_at"].isoformat(),
                "source_signals": args["sources"],
                "event_signals": [{"event_signal_id": "success",
                                   "source_signal_ids": ["new-source"]}],
            }, output_json={"event_resolutions": [resolution]},
        ))
        db.commit()
        assert len(recover_saved_technical_reviews(db)) == 1
        assert task.status == "pending"
        assert len(recover_saved_technical_reviews(db, dry_run=False)) == 1
        db.commit()
        assert recover_saved_technical_reviews(db, dry_run=False) == []


def test_ownership_and_recovery_roll_back_together():
    with make_session() as db:
        task, item = seed(db)
        technical(task)
        db.commit()
        assert recover_technical_reviews(db, **accepted(db, item))
        db.rollback()
        assert task.status == "pending" and item.event_id is None
        assert "automatic_recovery" not in task.payload_json


@pytest.mark.parametrize("has_error", [True, False])
def test_legacy_technical_flag_is_distinct_from_plain_weak_signal(has_error):
    with make_session() as db:
        task, item = seed(db)
        technical(task)
        task.payload_json["match_features_json"] = {
            "guardrail_flags": ["weak_signal_only"],
            "semantic_quality_flags": ["llm_invalid_response"] if has_error else [],
        }
        db.commit()
        assert bool(recover_technical_reviews(db, **accepted(db, item))) == has_error


def test_explicit_batch_selections_keep_their_own_manual_decisions_and_item_assignments():
    with make_session() as db:
        old, old_item = seed(db)
        technical(old, candidate_event_id="a")
        manual, new_item = seed(db, offset=1)
        db.commit()
        result = batch_merge_reviews(db, batch_request(manual, old))
        assert result["status"] == "succeeded", result
        assert [r["status"] for r in result["items"]] == ["succeeded", "succeeded"]
        assert old.status == manual.status == "resolved"
        assert old_item.event_id == new_item.event_id
        assert old.decision == "merge_to_existing" and "automatic_recovery" not in old.payload_json


def test_manual_acceptance_clears_related_technical_error_only():
    with make_session() as db:
        old, old_item = seed(db)
        technical(old)
        manual, _ = seed(db, offset=1)
        db.commit()
        decision = _decide_human_review_task(
            db, manual, HumanReviewDecisionInput(decision="merge_to_existing"),
        )
        assert decision.status == "succeeded"
        assert old.status == "cancelled" and old_item.event_id is None
        assert old.payload_json["automatic_recovery"]["human_review_id"] == manual.id
        assert manual.status == "resolved" and manual.decision == "merge_to_existing"


def test_real_analysis_pipeline_retires_old_failure_before_new_report(environment):
    db, client, _ = environment
    client.titles = [TITLE]
    client.urls[TITLE] = "https://www.zhihu.com/question/123"
    task = HumanReviewTask(review_type="event_merge_candidate", status="pending", payload_json={
        "event_signal_id": "failed", "observed_at": (START-timedelta(hours=1)).isoformat(),
        "source_citations": [{"url": client.urls[TITLE]}],
    })
    technical(task)
    db.add(task)
    db.commit()
    result = HotspotAnalysisAgent().run(db, request("recovered-round", START))
    assert result.status != "failed", result.model_dump()
    db.refresh(task)
    assert task.status == "cancelled"
    assert task.payload_json["automatic_recovery"]["run_id"] == "recovered-round"
