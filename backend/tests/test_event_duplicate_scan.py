"""Exhaustive maintenance must never inherit retrieval limits or merge by itself."""

from datetime import UTC, datetime, timedelta
from time import monotonic
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from test_llm_efficiency import runtime as runtime

from app.core.config import settings
from app.db.session import Base
from app.models import (
    AgentTask,
    Event,
    EventPairJudgment,
    EventSnapshot,
    HumanReviewTask,
    Item,
    Source,
)
from app.services.event_duplicate_scan import (
    create_scan,
    event_material,
    pair_key,
    recent_community_materials,
    run_slice,
)

NOW = datetime(2026, 11, 5, 8, 10, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "business.db"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def add_event(db, eid, *, observed=NOW, present=True, status="succeeded", official=False):
    event = Event(event_id=eid, title=f"具体事件{eid}", lifecycle_status="active",
                  event_detail_json={"event_domain": "official_agenda"} if official else {})
    db.add(event)
    db.flush()
    db.add(EventSnapshot(event_id=event.id, snapshot_at=observed, metrics_json={
        "platform_observations": {"weibo": [{
            "observed_at": observed.isoformat(), "run_id": "collection-1", "platform": "weibo",
            "source_id": "weibo_rsshub_hot_search", "topn_present": present,
            "collection_status": status,
        }]},
    }))
    db.commit()
    return event


def test_only_valid_recent_community_observations(db):
    add_event(db, "good")
    add_event(db, "old", observed=NOW - timedelta(hours=24))
    add_event(db, "future", observed=NOW + timedelta(seconds=1))
    add_event(db, "failed", status="failed")
    add_event(db, "absent", present=False)
    add_event(db, "unknown", present=None)
    add_event(db, "official", official=True)
    assert [v["document"]["source_id"] for v in recent_community_materials(db, NOW)] == ["good"]


def test_exhaustive_unordered_pairs_and_durable_cache(db, runtime, monkeypatch):
    client, calls = runtime
    monkeypatch.setattr(settings, "llm_candidate_limit", 1)
    for eid in ["d", "b", "a", "c"]:
        add_event(db, eid)
    task = create_scan(db, window_end=NOW)
    run_slice(db, task, client=client, max_batches=20)
    assert task.status == "completed"
    pairs = [(p["incoming"]["source_id"], c["source_id"])
             for p in calls["requests"] for c in p["candidates"]]
    assert pairs == [("a", "b"), ("a", "c"), ("a", "d"),
                     ("b", "c"), ("b", "d"), ("c", "d")]
    assert db.scalar(select(func.count()).select_from(EventPairJudgment)) == 6
    # Durable results survive expiry/removal of the ordinary 24-hour HTTP cache.
    with client._cache() as cache:
        cache.execute("DELETE FROM llm_results")
    count = len(calls["requests"])
    again = create_scan(db, window_end=NOW)
    run_slice(db, again, client=client, max_batches=20)
    assert again.output_json["cache_hits"] == 6
    assert len(calls["requests"]) == count


def test_pair_key_changes_with_material_model_and_prompt(db, runtime, monkeypatch):
    import app.services.event_duplicate_scan as scan

    client, _ = runtime
    a, b = add_event(db, "a"), add_event(db, "b")
    original = pair_key(client, event_material(a), event_material(b))
    a.summary = "新的正文细节"
    assert pair_key(client, event_material(a), event_material(b)) != original
    a.summary = None
    monkeypatch.setattr(settings, "deepseek_model", "changed-model")
    assert pair_key(client, event_material(a), event_material(b)) != original
    monkeypatch.undo()
    monkeypatch.setattr(scan, "MATCH_PROMPT", scan.MATCH_PROMPT + "新提示词")
    assert pair_key(client, event_material(a), event_material(b)) != original


def test_only_suspected_duplicates_reviewed_no_merge_and_review_reused(db, runtime):
    client, calls = runtime
    calls["same"] = {"b"}
    a, b = add_event(db, "a"), add_event(db, "b")
    task = create_scan(db, window_end=NOW)
    run_slice(db, task, client=client)
    review = db.scalar(select(HumanReviewTask))
    assert review.review_type == "event_duplicate_candidate"
    assert review.status == "pending"
    assert review.payload_json["run_ids"] == ["collection-1"]
    assert review.payload_json["event_pair_documents"][0]["source_id"] == "a"
    assert a.lifecycle_status == b.lifecycle_status == "active"
    task = create_scan(db, window_end=NOW)
    run_slice(db, task, client=client)
    assert db.scalar(select(func.count()).select_from(HumanReviewTask)) == 1


def test_batch_checkpoint_resume_without_repeating_completed_requests(db, runtime):
    client, calls = runtime
    for eid in ["a", "b", "c", "d"]:
        add_event(db, eid)
    task = create_scan(db, window_end=NOW)
    run_slice(db, task, client=client, max_batches=1, batch_size=2)
    assert task.status == "queued"
    assert task.output_json["completed_pairs"] == 2
    task_id = task.id
    db.expire_all()
    task = db.get(AgentTask, task_id)
    run_slice(db, task, client=client, max_batches=10, batch_size=2)
    assert task.status == "completed"
    assert sum(len(p["candidates"]) for p in calls["requests"]) == 6


def test_invalid_quotes_never_cached_or_reviewed_and_budget_is_durable(db, runtime):
    client, calls = runtime
    add_event(db, "a")
    add_event(db, "b")
    calls["invalid"] = True
    task = create_scan(db, window_end=NOW)
    run_slice(db, task, client=client)
    assert task.status == "paused"
    assert task.output_json["completed_pairs"] == 0
    assert db.scalar(select(func.count()).select_from(EventPairJudgment)) == 0
    assert db.scalar(select(func.count()).select_from(HumanReviewTask)) == 0
    client.max_calls = 1
    calls["invalid"] = False
    run_slice(db, task, client=client)
    assert task.status == "budget_exhausted"
    assert len(calls["requests"]) == 1


def test_empty_scope_has_no_api_requests(db, runtime):
    client, calls = runtime
    task = create_scan(db, window_end=NOW)
    run_slice(db, task, client=client)
    assert task.status == "completed"
    assert calls["requests"] == []


def test_changed_body_rejudged_but_observation_only_change_reuses_cache(db, runtime):
    client, calls = runtime
    a = add_event(db, "a")
    add_event(db, "b")
    task = create_scan(db, window_end=NOW)
    run_slice(db, task, client=client)
    a.last_seen_at = NOW + timedelta(seconds=1)
    db.commit()
    task = create_scan(db, window_end=NOW)
    run_slice(db, task, client=client)
    assert len(calls["requests"]) == 1
    a.summary = "正文增加新进展"
    db.commit()
    task = create_scan(db, window_end=NOW)
    run_slice(db, task, client=client)
    assert len(calls["requests"]) == 2


def test_manual_pair_merge_moves_members_only_after_explicit_confirmation(db, runtime):
    from app.schemas.human_review import HumanReviewDecisionInput
    from app.services.human_review import _decide_human_review_task

    client, calls = runtime
    calls["same"] = {"b"}
    a, b = add_event(db, "a"), add_event(db, "b")
    source = Source(name="weibo", source_type="community_hotlist", platform="weibo")
    item = Item(source=source, event=a, title="话题正文", content_hash="test-hash")
    db.add_all([source, item])
    db.commit()
    task = create_scan(db, window_end=NOW)
    run_slice(db, task, client=client)
    review = db.scalar(select(HumanReviewTask))
    assert item.event_id == a.id
    rejected = _decide_human_review_task(
        db, review, HumanReviewDecisionInput(decision="create_new_event"),
    )
    assert rejected.status == "failed"
    result = _decide_human_review_task(
        db, review, HumanReviewDecisionInput(decision="merge_to_existing"),
    )
    assert result.status == "succeeded"
    assert item.event_id == b.id
    assert a.lifecycle_status == "merged"
    assert a.event_detail_json["merged_into_event_id"] == "b"
    assert "a" in b.event_detail_json["merged_event_ids"]
    assert db.scalar(select(func.count()).select_from(EventSnapshot)) == 2


def test_stale_review_cannot_move_members(db, runtime):
    from app.schemas.human_review import HumanReviewDecisionInput
    from app.services.human_review import _decide_human_review_task

    client, calls = runtime
    calls["same"] = {"b"}
    a = add_event(db, "a")
    add_event(db, "b")
    task = create_scan(db, window_end=NOW)
    run_slice(db, task, client=client)
    review = db.scalar(select(HumanReviewTask))
    a.title = "新事件材料"
    db.commit()
    result = _decide_human_review_task(
        db, review, HumanReviewDecisionInput(decision="merge_to_existing"),
    )
    assert result.status == "failed"
    assert review.status == "pending"
    assert a.lifecycle_status == "active"


def test_yield_for_collection_makes_no_paid_call(db, runtime):
    client, calls = runtime
    add_event(db, "a")
    add_event(db, "b")
    task = create_scan(db, window_end=NOW)
    run_slice(db, task, client=client, should_yield=lambda: True)
    assert task.status == "queued"
    assert calls["requests"] == []


def test_schedule_is_eight_hours_and_never_resets_active_budget(db, runtime, monkeypatch):
    from app.services.event_duplicate_jobs import queued_scan

    monkeypatch.setattr(settings, "event_duplicate_scan_enabled", False)
    assert queued_scan(db, NOW) is None
    monkeypatch.setattr(settings, "event_duplicate_scan_enabled", True)
    task = queued_scan(db, NOW)
    task.created_at = NOW
    db.commit()
    assert queued_scan(db, NOW + timedelta(hours=1)).id == task.id
    task.status = "budget_exhausted"
    db.commit()
    assert queued_scan(db, NOW + timedelta(hours=7, minutes=59)) is None
    assert queued_scan(db, NOW + timedelta(hours=8)).id != task.id


def test_stale_process_recovers_cursor_with_same_run_id(db, runtime):
    from app.services.event_duplicate_jobs import PREFIX, interrupt_scan

    task = create_scan(db, window_end=NOW)
    run_id = task.input_json["run_id"]
    task.status = "running"
    task.output_json = {**task.output_json, "cursor": [1, 2]}
    db.commit()
    interrupt_scan(db, PREFIX + str(task.id), "wall_clock_timeout")
    assert task.status == "queued"
    assert task.output_json["cursor"] == [1, 2]
    assert task.input_json["run_id"] == run_id
    task.status = "running"
    db.commit()
    interrupt_scan(db, PREFIX + str(task.id), "wall_clock_timeout")
    assert task.status == "partial"


def test_only_one_recovery_pass_and_successes_never_paid_twice(db, runtime):
    client, calls = runtime
    for eid in ["a", "b", "c"]:
        add_event(db, eid)
    task = create_scan(db, window_end=NOW)
    calls["invalid"] = True
    run_slice(db, task, client=client, max_batches=10)
    assert task.status == "paused"
    assert task.output_json["unresolved_pairs"]
    calls["invalid"] = False
    run_slice(db, task, client=client, max_batches=10)
    assert task.status == "completed"
    assert task.retry_count == 1
    assert task.output_json["completed_pairs"] == 3
    assert not task.output_json["unresolved_pairs"]


def coordinator_for(db, monkeypatch):
    from app.services import briefing_jobs, email_settings, overnight_test

    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(overnight_test, "record_resources", lambda db: None)
    monkeypatch.setattr(overnight_test, "tick", lambda db, now: False)
    monkeypatch.setattr(email_settings, "get_email_settings", lambda *a, **kw: SimpleNamespace(
        daily_briefing_enabled=False, email_schedule_enabled=False,
    ))
    return briefing_jobs.JobCoordinator(factory=sessionmaker(bind=db.get_bind()))


def test_coordinator_gives_normal_collection_priority(db, runtime, monkeypatch):
    from app.models.briefing import AnalysisRun
    from app.services import briefing_jobs

    coordinator = coordinator_for(db, monkeypatch)
    db.add(AnalysisRun(run_id="normal-collection", status="queued", started_at=NOW, input_json={}))
    db.commit()
    launched = []
    monkeypatch.setattr(briefing_jobs.subprocess, "Popen",
                        lambda args, **kw: launched.append(args) or SimpleNamespace(stdout=None))
    coordinator.tick(NOW)
    assert launched[0][-1] == "normal-collection"
    assert db.scalar(select(func.count()).select_from(AgentTask)) == 0


def test_running_maintenance_does_not_block_collection_reservations(db, runtime, monkeypatch):
    from app.models.briefing import AnalysisRun
    from app.services import overnight_test
    from app.services.event_duplicate_jobs import PREFIX

    coordinator = coordinator_for(db, monkeypatch)
    coordinator.process = SimpleNamespace(poll=lambda: None)
    coordinator.process_run_id = PREFIX + "123"
    coordinator.process_started = monotonic()

    def due(db, now):
        db.add(AnalysisRun(run_id="due-collection", status="queued", started_at=now, input_json={}))
        db.commit()
        return True

    monkeypatch.setattr(overnight_test, "tick", due)
    coordinator.tick(NOW)
    assert db.get(AnalysisRun, "due-collection").status == "queued"


def test_idle_coordinator_dispatches_maintenance_through_existing_worker(db, runtime, monkeypatch):
    from app.services import briefing_jobs
    from app.services.event_duplicate_jobs import PREFIX

    coordinator = coordinator_for(db, monkeypatch)
    monkeypatch.setattr(settings, "event_duplicate_scan_enabled", True)
    add_event(db, "a")
    add_event(db, "b")
    launched = []
    monkeypatch.setattr(briefing_jobs.subprocess, "Popen",
                        lambda args, **kw: launched.append(args) or SimpleNamespace(stdout=None))
    coordinator.tick(NOW)
    task = db.scalar(select(AgentTask))
    assert launched[0][-1] == PREFIX + str(task.id)
    assert task.output_json["pair_count"] == 1
    assert task.input_json["token_budget"] == settings.event_duplicate_token_budget_per_scan


def test_provider_outage_stops_instead_of_requesting_every_pair(db, runtime, monkeypatch):
    from app.services import event_duplicate_scan
    from app.services.llm_client import LLMFailure

    client, _ = runtime
    for eid in ["a", "b", "c", "d"]:
        add_event(db, eid)
    task = create_scan(db, window_end=NOW)
    attempts = []

    def unavailable(*args, **kwargs):
        attempts.append(1)
        raise LLMFailure("llm_http_503")

    monkeypatch.setattr(event_duplicate_scan, "judge_pairs", unavailable)
    run_slice(db, task, client=client, max_batches=100)
    assert len(attempts) == 1
    assert task.status == "paused"
    run_slice(db, task, client=client, max_batches=100)
    assert len(attempts) == 2
    assert task.status == "partial"


def test_pending_cached_pair_follows_new_report_scope_and_resolved_pair_stays_resolved(db, runtime):
    client, calls = runtime
    calls["same"] = {"b"}
    add_event(db, "a")
    add_event(db, "b")
    task = create_scan(db, window_end=NOW)
    run_slice(db, task, client=client)
    review = db.scalar(select(HumanReviewTask))
    for snapshot in db.scalars(select(EventSnapshot)):
        metrics = snapshot.metrics_json
        obs = {**metrics["platform_observations"]["weibo"][0], "run_id": "collection-2"}
        snapshot.metrics_json = {"platform_observations": {"weibo": [obs]}}
    db.commit()
    task = create_scan(db, window_end=NOW)
    run_slice(db, task, client=client)
    assert review.payload_json["run_ids"] == ["collection-1", "collection-2"]
    assert len(calls["requests"]) == 1
    review.status, review.decision = "resolved", "reject"
    db.commit()
    task = create_scan(db, window_end=NOW)
    run_slice(db, task, client=client)
    assert task.output_json["review_ids"] == []
    assert db.scalar(select(func.count()).select_from(HumanReviewTask)) == 1
