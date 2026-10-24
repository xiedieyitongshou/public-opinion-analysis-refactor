"""Batch approval must preserve exclusive ownership and rebuild only once."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from test_day41_human_review_queue import client_for, make_session, make_session_factory
from test_hotspot_analysis import environment as environment
from test_week08_briefing import run_report

from app.api.briefing import retry_review_refresh, review_queue
from app.models import DailyReport, Event, EventSnapshot, HumanReviewTask, Item, Source
from app.schemas.human_review import BatchMergeInput, HumanReviewDecisionInput
from app.services.human_review import _decide_human_review_task
from app.services.review_batch import batch_merge_reviews, selection_metadata


def seed(db, *, question=123, target="a", offset=0, owner=None, weibo=False):
    event = db.scalar(select(Event).where(Event.event_id == target))
    if event is None:
        event = Event(event_id=target, title=f"目标{target}", lifecycle_status="active")
        db.add(event)
        db.flush()
    source = db.scalar(select(Source).where(Source.name == "fixture"))
    if source is None:
        source = Source(
            name="fixture", source_type="community_hotlist", platform="weibo" if weibo else "zhihu"
        )
        db.add(source)
        db.flush()
    now = datetime(2026, 10, 21, 12, tzinfo=UTC) + timedelta(hours=offset)
    item = Item(
        source_id=source.id,
        title="相同话题" if weibo else f"问题{question}",
        url="https://s.weibo.com/weibo?q=test"
        if weibo
        else f"https://www.zhihu.com/question/{question}",
        content_hash=f"{question}-{offset}-{target}",
        fetched_at=now,
        signal_role="topic_discovery_signal",
        event_id=owner,
    )
    db.add(item)
    db.flush()
    task = HumanReviewTask(
        review_type="event_merge_candidate",
        status="pending",
        payload_json={
            "title": item.title,
            "item_ids": [str(item.id)],
            "candidate_event_id": target,
            "event_signal_id": f"signal-{item.id}",
            "run_id": f"round-{offset}",
            "observed_at": now.isoformat(),
            "source_citations": [{"url": item.url, "title": item.title}],
        },
    )
    db.add(task)
    db.commit()
    return task, item


def request(*tasks, **overrides):
    return BatchMergeInput(
        items=[
            {"task_id": t.id, "candidate_event_id": t.payload_json["candidate_event_id"]}
            for t in tasks
        ],
        **overrides,
    )


def test_cross_round_alternatives_are_exclusive_before_any_writes():
    with make_session() as db:
        a, ia = seed(db, target="a")
        b, ib = seed(db, target="b", offset=1)
        metadata = selection_metadata(db, [a])
        assert metadata[a.id]["conflicts"] == [{"task_id": b.id, "target_title": "目标b"}]
        result = batch_merge_reviews(db, request(a, b))
        assert result["status"] == "failed"
        assert result["conflicts"][0]["task_ids"] == [a.id, b.id]
        assert a.status == b.status == "pending"
        assert ia.event_id is ib.event_id is None
        assert not db.scalar(select(Event).where(Event.event_id == "a")).event_detail_json


def test_shared_item_is_exclusive_even_with_different_topic_identity():
    with make_session() as db:
        a, _ = seed(db, target="a")
        b, _ = seed(db, target="b", question=999)
        b.payload_json = {**b.payload_json, "item_ids": a.payload_json["item_ids"]}
        db.commit()
        assert not batch_merge_reviews(db, request(a, b))["items"]
        assert a.status == b.status == "pending"


def test_different_topics_same_target_merge_and_rebuild_once(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.services.review_refresh.refresh_after_reviews",
        lambda db, rows: calls.append([t.id for t, _ in rows]) or 91,
    )
    with make_session() as db:
        a, ia = seed(db)
        b, ib = seed(db, question=456, offset=1)
        result = batch_merge_reviews(db, request(a, b, reviewer="tester"))
        assert result["status"] == "succeeded" and result["report_id"] == 91
        assert calls == [[a.id, b.id]]
        assert a.status == b.status == "resolved"
        assert ia.event_id == ib.event_id
        assert a.payload_json["decision_audit"]["reviewer"] == "tester"
        assert b.payload_json["refresh"]["batch_review_ids"] == [a.id, b.id]
        assert batch_merge_reviews(db, request(a, b))["status"] == "failed"
        assert len(calls) == 1


def test_duplicate_same_topic_same_target_is_not_exclusive(monkeypatch):
    monkeypatch.setattr("app.services.review_refresh.refresh_after_reviews", lambda *_: None)
    with make_session() as db:
        a, ia = seed(db)
        b, ib = seed(db, offset=1)
        assert not selection_metadata(db, [a])[a.id]["conflicts"]
        result = batch_merge_reviews(db, request(a, b))
        assert result["status"] == "succeeded"
        assert a.status == b.status == "resolved"
        assert ia.event_id == ib.event_id


def test_existing_cross_round_ownership_blocks_single_and_batch():
    with make_session() as db:
        a, ia = seed(db, target="a")
        a.status = "resolved"
        ia.event_id = db.scalar(select(Event).where(Event.event_id == "a")).id
        db.commit()
        b, ib = seed(db, target="b", offset=1)
        assert not selection_metadata(db, [b])[b.id]["eligible"]
        assert batch_merge_reviews(db, request(b))["status"] == "failed"
        single = _decide_human_review_task(
            db, b, HumanReviewDecisionInput(decision="merge_to_existing")
        )
        assert single.status == "failed" and "跨轮" in single.message
        assert b.status == "pending" and ib.event_id is None


def test_weibo_repeated_title_is_scoped_to_24_hours(monkeypatch):
    monkeypatch.setattr("app.services.review_refresh.refresh_after_reviews", lambda *_: None)
    with make_session() as db:
        a, _ = seed(db, weibo=True)
        b, _ = seed(db, target="b", offset=25, weibo=True)
        assert not selection_metadata(db, [a])[a.id]["conflicts"]
        assert batch_merge_reviews(db, request(a, b))["status"] == "succeeded"


@pytest.mark.parametrize("invalid", ["target", "missing", "duplicate", "resolved"])
def test_invalid_or_stale_batch_rejects_all(invalid):
    with make_session() as db:
        a, item = seed(db)
        data = request(a)
        if invalid == "target":
            data.items[0].candidate_event_id = "changed"
        elif invalid == "missing":
            data.items[0].task_id = 9999
        elif invalid == "duplicate":
            data.items.append(data.items[0])
        else:
            a.status = "resolved"
            db.commit()
        assert batch_merge_reviews(db, data)["status"] == "failed"
        assert item.event_id is None


def test_failed_refresh_retry_covers_the_whole_batch(monkeypatch):
    def fail(*_):
        raise RuntimeError("temporary rebuild failure")

    monkeypatch.setattr("app.services.review_refresh.refresh_after_reviews", fail)
    with make_session() as db:
        a, _ = seed(db)
        b, _ = seed(db, question=456, offset=1)
        result = batch_merge_reviews(db, request(a, b))
        assert result["refresh_status"] == "failed"
        assert a.status == b.status == "resolved"
        assert review_queue(db, limit=50)["total"] == 2
        calls = []
        monkeypatch.setattr(
            "app.services.review_refresh.refresh_after_reviews",
            lambda db, rows: calls.append([t.id for t, _ in rows]) or 99,
        )
        assert retry_review_refresh(a.id, db)["report_id"] == 99
        assert calls == [[a.id, b.id]]
        assert b.payload_json["refresh"]["status"] == "refreshed"
        assert review_queue(db, limit=50)["total"] == 0


def test_api_queue_metadata_and_batch_preflight():
    factory = make_session_factory()
    with factory() as db:
        a, _ = seed(db)
        b, _ = seed(db, target="b", offset=1)
        data = request(a, b).model_dump()
    client = client_for(factory)
    response = client.get("/api/review-queue")
    assert response.status_code == 200
    assert response.json()["items"][0]["merge_selection"]["conflicts"][0]["task_id"] == b.id
    page = client.get("/api/review-queue?limit=1&offset=1").json()
    assert page["total"] == 2 and page["offset"] == 1 and page["items"][0]["id"] == b.id
    assert page["items"][0]["merge_selection"]["conflicts"][0]["task_id"] == a.id
    result = client.post("/ops/human-review/batch-merge", json=data)
    assert result.status_code == 200 and result.json()["status"] == "failed"
    assert client.post("/ops/human-review/batch-merge", json={"items": []}).status_code == 422
    client.app.dependency_overrides.clear()


def test_batch_rebuild_preserves_both_events_original_heat_and_one_new_draft(
    environment, monkeypatch
):
    from copy import deepcopy

    from sqlalchemy import func

    import app.services.review_refresh as refresh

    db, client, _ = environment
    client.titles = ["热议", "热点"]
    client.urls = {
        "热议": "https://www.zhihu.com/question/123",
        "热点": "https://www.zhihu.com/question/456",
    }
    original = run_report(environment, "batch-original")
    original_json = deepcopy(original.briefing_json)
    tasks = list(db.scalars(select(HumanReviewTask).where(HumanReviewTask.status == "pending")))
    assert len(tasks) == 2
    for n, task in enumerate(tasks):
        db.add(Event(event_id=f"target-{n}", title=f"审核事件{n}", lifecycle_status="active"))
        task.payload_json = {**task.payload_json, "candidate_event_id": f"target-{n}"}
    db.commit()
    before_reports = db.scalar(select(func.count(DailyReport.id)))
    before_calls = client.calls
    recorded = []
    record = refresh.record_collection_round

    def record_once(*args, **kwargs):
        recorded.append({event.event_id for event in kwargs["tracked_events"]})
        return record(*args, **kwargs)

    monkeypatch.setattr(refresh, "record_collection_round", record_once)
    result = batch_merge_reviews(db, request(*tasks))
    assert result["refresh_status"] == "refreshed", result
    assert recorded == [{"target-0", "target-1"}]
    assert client.calls == before_calls
    assert db.scalar(select(func.count(DailyReport.id))) == before_reports + 1
    assert original.briefing_json == original_json
    updated = db.get(DailyReport, result["report_id"])
    assert updated.briefing_json["event_count"] == 2
    assert updated.briefing_json["pending_review_count"] == 0
    for event in db.scalars(select(Event)):
        assert (
            db.scalar(select(EventSnapshot).where(EventSnapshot.event_id == event.id)) is not None
        )


def test_busy_collection_prevents_batch_and_unknown_titles_do_not_imply_identity():
    from app.services.briefing_jobs import acquire_lease
    from app.services.review_batch import same_review_subject

    with make_session() as db:
        assert not same_review_subject(db, {"title": "热议"}, {"title": "热议"})
        task, item = seed(db)
        assert acquire_lease(db, "analysis", "other-worker", seconds=900)
        assert batch_merge_reviews(db, request(task))["status"] == "failed"
        assert task.status == "pending" and item.event_id is None
