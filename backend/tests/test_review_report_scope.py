"""Report-bound review queues and updates must not leak across editions."""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from test_day41_human_review_queue import client_for, make_session, make_session_factory
from test_hotspot_analysis import environment as environment
from test_review_batch import request as batch_request
from test_review_batch import seed
from test_week08_briefing import run_report

from app.api.briefing import report_info, review_queue
from app.models import AgentTask, DailyReport, HumanReviewTask
from app.models.briefing import AnalysisRun, ReportRevision
from app.schemas.human_review import HumanReviewDecisionInput
from app.services.human_review import decide_human_review_task
from app.services.review_batch import batch_merge_reviews
from app.services.review_refresh import refresh_after_review
from app.services.review_scope import report_review_scope, review_refresh_run

NOW = datetime(2026, 10, 31, 1, tzinfo=UTC)


def edition(db, key, *, children=None, supersedes=None):
    run = db.get(AnalysisRun, key)
    if run is None:
        run = AnalysisRun(
            run_id=key, status="partial", started_at=NOW, window_end=NOW,
            input_json={"report_window": {"run_ids": children}} if children else {},
            output_json={"run_id": key},
        )
        db.add(run)
    report = DailyReport(
        report_date=date(2026, 10, 31), status="draft", title=key,
        briefing_json={"run_id": key, "window_start": (NOW-timedelta(hours=2)).isoformat(),
                       "window_end": NOW.isoformat()},
    )
    db.add(report)
    db.flush()
    db.add(ReportRevision(report_id=report.id, run_id=key, supersedes_id=supersedes,
                          fingerprint=f"fixture-{key}-{report.id}"))
    run.report_id = report.id
    db.commit()
    return report


def test_queue_filters_before_pagination_and_includes_cross_round_relations():
    with make_session() as db:
        first = edition(db, "round-0")
        second = edition(db, "round-1")
        summary = edition(db, "summary", children=["round-0", "round-1"])
        a, _ = seed(db, question=101)
        shared, _ = seed(db, question=102)
        shared.payload_json = {**shared.payload_json, "run_ids": ["round-0", "round-1"]}
        b, _ = seed(db, question=103, offset=1)
        outside, _ = seed(db, question=104, offset=9)
        db.commit()
        page = review_queue(db, limit=1, offset=1, report_id=first.id)
        assert page["total"] == 2 and page["items"][0].id == shared.id
        assert page["scope"]["run_id"] == "round-0"
        def ids(report):
            return [t.id for t in review_queue(db, limit=50, report_id=report.id)["items"]]
        assert ids(first) == [a.id, shared.id]
        assert ids(second) == [shared.id, b.id]
        assert ids(summary) == [a.id, shared.id, b.id]
        assert outside.id not in ids(summary)
        assert report_info(db, summary)["review_scope"]["pending_count"] == 3
        assert summary.briefing_json["run_id"] == "summary"


def test_revisions_share_scope_and_resolved_refresh_errors_stay_in_their_edition():
    with make_session() as db:
        old = edition(db, "round-0")
        new = edition(db, "round-0", supersedes=old.id)
        edition(db, "round-1")
        task, _ = seed(db)
        task.status = "resolved"
        task.payload_json = {**task.payload_json, "refresh": {"status": "failed"}}
        db.commit()
        for report in (old, new):
            queue = review_queue(db, limit=50, report_id=report.id)
            assert queue["total"] == 1 and queue["pending_count"] == 0
            assert queue["refresh_failed_count"] == 1
            assert queue["scope"]["latest_report_id"] == new.id


def test_unbound_legacy_and_missing_provenance_are_visible_without_guessing_titles():
    with make_session() as db:
        report = edition(db, "round-0")
        known, _ = seed(db)
        orphan, _ = seed(db, question=999, offset=8)
        orphan.payload_json = {"title": known.payload_json["title"]}
        legacy, _ = seed(db, question=998, offset=7)
        task = AgentTask(task_type="create_human_review_task",
                         status="succeeded", input_json={"run_id": "round-0"})
        db.add(task)
        db.flush()
        legacy.agent_task_id = task.id
        legacy.payload_json = {"title": "老审核记录"}
        db.commit()
        queue = review_queue(db, limit=50, report_id=report.id)
        assert [t.id for t in queue["items"]] == [known.id, legacy.id]
        assert [t.id for t in review_queue(db, limit=50, unbound=True)["items"]] == [orphan.id]


def test_api_report_scope_and_invalid_combinations():
    factory = make_session_factory()
    with factory() as db:
        report = edition(db, "round-0")
        task, _ = seed(db)
        seed(db, question=2, offset=1)
        report_id, task_id = report.id, task.id
    client = client_for(factory)
    response = client.get(f"/api/review-queue?report_id={report_id}&limit=1")
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == task_id
    assert response.json()["total"] == 1
    assert client.get("/api/reports").json()[0]["review_scope"]["total"] == 1
    assert client.get("/api/review-queue?report_id=99999").status_code == 404
    assert client.get(f"/api/review-queue?report_id={report_id}&unbound=true").status_code == 422
    client.app.dependency_overrides.clear()


def test_cross_edition_single_and_batch_decisions_reject_before_ownership_changes():
    with make_session() as db:
        first = edition(db, "round-0")
        a, item_a = seed(db)
        b, item_b = seed(db, question=2, offset=1)
        result = decide_human_review_task(db, b, HumanReviewDecisionInput(
            decision="merge_to_existing", report_id=first.id,
        ))
        assert result.status == "failed" and "不属于所选简报" in result.message
        batch = batch_request(a, b, report_id=first.id)
        assert batch_merge_reviews(db, batch)["status"] == "failed"
        assert a.status == b.status == "pending"
        assert item_a.event_id is item_b.event_id is None


def test_implicit_refresh_picks_related_report_instead_of_globally_latest():
    with make_session() as db:
        first = edition(db, "round-0")
        summary = edition(db, "old-summary", children=["round-0"])
        edition(db, "unrelated-latest")
        task, _ = seed(db)
        assert review_refresh_run(db, [task]).run_id == "old-summary"
        assert review_refresh_run(db, [task], first.id).run_id == "round-0"
        assert report_review_scope(db, summary.id).run_ids == ["old-summary", "round-0"]


def test_retry_retains_original_requested_report_context(monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.review_refresh.refresh_after_reviews",
                        lambda db, rows, **kw: calls.append(kw) or 55)
    with make_session() as db:
        task, _ = seed(db)
        task.status = "resolved"
        task.payload_json = {**task.payload_json, "refresh": {"requested_report_id": 12}}
        db.commit()
        assert refresh_after_review(db, task, "a") == 55
        assert calls == [{"report_id": 12}]


def test_interrupted_single_review_keeps_edition_for_retry(monkeypatch):
    class Interrupted(BaseException):
        pass

    def interrupt(*args, **kwargs):
        raise Interrupted()

    monkeypatch.setattr("app.services.review_refresh.refresh_after_review", interrupt)
    with make_session() as db:
        report = edition(db, "round-0")
        task, _ = seed(db)
        with pytest.raises(Interrupted):
            decide_human_review_task(db, task, HumanReviewDecisionInput(
                decision="merge_to_existing", report_id=report.id,
            ))
        db.refresh(task)
        assert task.status == "resolved"
        assert task.payload_json["refresh"]["requested_report_id"] == report.id
        assert task.payload_json["refresh"]["status"] == "failed"


def test_older_report_review_updates_only_that_edition_and_keeps_snapshots(
    environment, monkeypatch,
):
    import app.services.review_refresh as refresh
    from app.core.config import settings
    from app.services.llm_client import reasoning_mode

    db, collector, _ = environment
    collector.titles = ["热议"]
    first = run_report(environment, "old-edition")
    first_snapshot = deepcopy(first.briefing_json)
    task = db.scalar(select(HumanReviewTask).where(HumanReviewTask.status == "pending"))
    assert task is not None
    collector.time += timedelta(hours=3)
    collector.titles = ["另一热议"]
    latest = run_report(environment, "unrelated-edition")
    latest_snapshot = deepcopy(latest.briefing_json)
    calls_before = collector.calls
    modes = []
    classify = refresh.classify_events

    def spy(*args, **kwargs):
        modes.append(reasoning_mode())
        return classify(*args, **kwargs)

    monkeypatch.setattr(refresh, "classify_events", spy)
    monkeypatch.setattr(settings, "event_reasoning_mode", "llm")
    result = decide_human_review_task(db, task, HumanReviewDecisionInput(
        decision="create_new_event", event_title="审核确认的旧话题", report_id=first.id,
    ))
    assert result.status == "succeeded" and result.refresh_status == "refreshed", result
    assert db.get(ReportRevision, result.report_id).run_id == "old-edition"
    assert db.get(AnalysisRun, "unrelated-edition").report_id == latest.id
    assert first.briefing_json == first_snapshot and latest.briefing_json == latest_snapshot
    assert collector.calls == calls_before
    assert modes == ["rules"]  # Editing an older rules report does not switch its reasoning mode.
    assert db.get(DailyReport, result.report_id).briefing_json["pending_review_count"] == 0
