from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from test_day41_human_review_queue import candidate_review_payload, make_session
from test_semantic_merge_policy import event_signal

from app.models import HumanReviewTask
from app.models.briefing import AnalysisRun
from app.schemas import CreateHumanReviewTaskInput, EventResolution
from app.services.human_review import (
    _decide_human_review_task,
    consolidate_pending_reviews,
    create_human_review_tasks,
    reuse_reviewed_merge,
    same_pending_relation,
)
from app.services.review_identity import relation_key, topic_identity


def payload(run="one", signal="signal-one", url="https://www.zhihu.com/question/123"):
    return {
        **candidate_review_payload(event_signal_id=signal),
        "run_id": run,
        "source_citations": [{"url": url}],
    }


def test_platform_identity_and_preserved_article_query():
    assert topic_identity(payload()) == topic_identity(
        payload(url="https://www.zhihu.com/question/123/answer/456?utm_source=test")
    )
    a = payload(url="https://www.chinanews.com.cn/story?id=1&utm_source=a")
    b = payload(url="https://www.chinanews.com.cn/story?id=1&utm_source=b")
    assert topic_identity(a) == topic_identity(b)
    assert topic_identity(a) != topic_identity(
        payload(url="https://www.chinanews.com.cn/story?id=2")
    )
    assert topic_identity(payload()) != topic_identity(
        payload(url="https://www.zhihu.com/question/124")
    )


def test_real_run_scoped_signal_ids_deduplicate_and_keep_evidence():
    with make_session() as db:
        for number in range(5):
            p = payload(str(number), f"event-sig-{number}")
            create_human_review_tasks(
                db,
                CreateHumanReviewTaskInput(
                    run_id=p["run_id"],
                    candidate_review=p,
                    source_citations=p["source_citations"],
                ),
            )
        rows = list(db.scalars(select(HumanReviewTask)))
        assert len(rows) == 1
        assert len(rows[0].payload_json["run_ids"]) == 5
        assert len(rows[0].payload_json["event_signal_ids"]) == 5
        assert len(rows[0].payload_json["evidence_history"]) == 5


def test_old_duplicates_are_archived_with_original_evidence():
    with make_session() as db:
        db.add_all(
            [
                HumanReviewTask(
                    review_type="event_merge_candidate",
                    status="pending",
                    payload_json=payload(str(i), f"sig-{i}"),
                )
                for i in range(5)
            ]
        )
        db.commit()
        assert consolidate_pending_reviews(db) == 4
        db.commit()
        rows = list(db.scalars(select(HumanReviewTask).order_by(HumanReviewTask.id)))
        assert rows[0].status == "pending"
        assert len(rows[0].payload_json["run_ids"]) == 5
        assert all(r.status == "cancelled" for r in rows[1:])
        assert rows[1].payload_json["event_signal_id"] == "sig-1"
        assert consolidate_pending_reviews(db) == 0


def test_conflict_or_target_change_stays_separate():
    a, b = payload(), payload("two", "signal-two")
    assert relation_key("event_merge_candidate", a) == relation_key("event_merge_candidate", b)
    b["candidate_event_id"] = "another"
    assert relation_key("event_merge_candidate", a) != relation_key("event_merge_candidate", b)

    b = payload("two", "signal-two")
    b["match_features_json"]["guardrail_flags"].append("time_conflict")
    assert relation_key("event_merge_candidate", a) != relation_key("event_merge_candidate", b)


def test_one_decision_groups_existing_duplicates_without_deciding_other_target():
    from app.schemas import HumanReviewDecisionInput

    with make_session() as db:
        a = HumanReviewTask(
            review_type="event_merge_candidate", status="pending", payload_json=payload()
        )
        b = HumanReviewTask(
            review_type="event_merge_candidate",
            status="pending",
            payload_json=payload("two", "different"),
        )
        c = HumanReviewTask(
            review_type="event_merge_candidate",
            status="pending",
            payload_json={**payload("three", "third"), "candidate_event_id": "another"},
        )
        db.add_all([a, b, c])
        db.commit()
        result = _decide_human_review_task(db, a, HumanReviewDecisionInput(decision="reject"))
        assert result.status == "succeeded" and a.status == "resolved"
        assert b.status == "cancelled" and b.payload_json["grouped_into_review_id"] == a.id
        assert c.status == "pending"


def test_weibo_reuse_is_bounded_and_generic_titles_do_not_identify():
    with make_session() as db:
        now = datetime(2026, 10, 20, tzinfo=UTC)
        for run, hours in (("one", 0), ("two", 2), ("later", 25)):
            db.add(
                AnalysisRun(
                    run_id=run,
                    input_json={},
                    status="partial",
                    started_at=now + timedelta(hours=hours),
                )
            )
        db.commit()
        a = payload(url="https://m.weibo.cn/search?q=topic")
        a["title"] = "#台风登陆#"
        b = {**a, "run_id": "two", "event_signal_id": "different", "title": "台风登陆"}
        assert same_pending_relation(db, "event_merge_candidate", a, b)
        b["run_id"] = "later"
        assert not same_pending_relation(db, "event_merge_candidate", a, b)
        assert topic_identity({"title": "same", "event_signal_id": "a"}) != topic_identity(
            {"title": "same", "event_signal_id": "b"}
        )


def test_decision_reuse_across_runs_and_ignore_is_not_permanent():
    with make_session() as db:
        task = HumanReviewTask(
            review_type="event_merge_candidate",
            status="resolved",
            decision="merge_to_existing",
            payload_json=payload(),
        )
        db.add(task)
        db.commit()
        signal = event_signal()
        signal.event_signal_id = "new-round-signal"
        resolution = EventResolution(
            event_signal_id=signal.event_signal_id,
            event_id="evt-existing",
            event_fingerprint="x",
            action="candidate_review",
            confidence=0.7,
            reason="review",
        )
        sources = [{"url": "https://www.zhihu.com/question/123"}]
        assert reuse_reviewed_merge(db, signal, resolution, sources, "two").action == "merge"
        task.decision = "reject"
        db.commit()
        assert reuse_reviewed_merge(db, signal, resolution, sources, "two").action == "reject"
        task.decision = "ignore"
        db.commit()
        assert (
            reuse_reviewed_merge(db, signal, resolution, sources, "two").action
            == "candidate_review"
        )
        task.decision = "merge_to_existing"
        db.commit()
        resolution.match_features_json.guardrail_flags = ["time_conflict"]
        assert (
            reuse_reviewed_merge(db, signal, resolution, sources, "two").action
            == "candidate_review"
        )
