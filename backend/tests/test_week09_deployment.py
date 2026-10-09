"""Deployment lifecycle: calendar scheduling, crash cleanup and persistent exact cache."""

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from test_hotspot_analysis import START, request
from test_hotspot_analysis import environment as environment
from test_week08_briefing import configure_email, run_report

from app.core.config import settings
from app.models import AgentTask, AgentToolCall, DailyReport
from app.models.briefing import AnalysisRun, DailyBriefingJob, EmailDelivery, ReportRevision
from app.schemas.analysis import HotspotAnalysisInput
from app.services.briefing_jobs import (
    JobCoordinator,
    execute_analysis,
    finish_interrupted_tasks,
    queue_analysis,
)
from app.services.daily_schedule import tick_daily
from app.services.runtime_logging import DailyLogHandler
from app.services.semantic_cache import RerankCache


def configure_daily(monkeypatch, tmp_path, client):
    configure_email(monkeypatch)
    monkeypatch.setattr(settings, "email_send_time", "08:00")
    monkeypatch.setattr(settings, "email_schedule_enabled", True)
    monkeypatch.setattr(settings, "daily_briefing_enabled", True)
    monkeypatch.setattr(settings, "briefing_artifact_dir", str(tmp_path / "artifacts"))
    monkeypatch.setattr("app.services.briefing_jobs.default_analysis_input",
                        lambda run_id=None: request(run_id, client.time))


def complete_queued(db, job):
    row = db.get(AnalysisRun, job.run_id)
    execute_analysis(db, HotspotAnalysisInput.model_validate(row.input_json))


def test_daily_generates_fresh_snapshot_sends_once_and_next_day(environment, monkeypatch, tmp_path):
    db, client, _ = environment
    configure_daily(monkeypatch, tmp_path, client)
    assert tick_daily(db, now=START - timedelta(hours=5)) is None  # 07:00 CST
    old_report = run_report(environment, "ordinary-sample")
    job = tick_daily(db, now=START)
    assert job.run_id == "daily-2026-09-28"
    complete_queued(db, job)
    sent = []

    def sender(message, recipient):
        sent.append(message["Message-ID"])
        assert message.get_body(preferencelist=("html",))
        return "sent", None

    assert tick_daily(db, now=START, sender=sender).status == "sent"
    db.expire_all()  # Restart/reload from durable records.
    assert tick_daily(db, now=START + timedelta(minutes=3), sender=sender).status == "sent"
    assert len(sent) == 1 and job.report_id != old_report.id
    approval = db.get(ReportRevision, job.report_id).approval_json
    assert approval["actor"] == "daily_schedule"
    assert len(list((tmp_path / "artifacts").rglob("report-*.*"))) == 3
    client.time += timedelta(days=1)
    tomorrow = tick_daily(db, now=client.time)
    assert tomorrow.run_id != job.run_id
    complete_queued(db, tomorrow)
    tick_daily(db, now=client.time, sender=sender)
    assert len(sent) == 2 and len(set(sent)) == 2


@pytest.mark.parametrize("failure", ["outage", "timeout", "stale"])
def test_daily_blocks_bad_or_old_snapshots(environment, monkeypatch, tmp_path, failure):
    db, client, _ = environment
    configure_daily(monkeypatch, tmp_path, client)
    job = tick_daily(db, now=START)
    if failure == "outage":
        client.failed = True
    if failure == "timeout":
        row = db.get(AnalysisRun, job.run_id)
        row.status, row.error = "failed", "wall_clock_timeout"
        db.commit()
    else:
        complete_queued(db, job)
    sent = []
    result = tick_daily(db, now=START + timedelta(hours=7 if failure == "stale" else 0),
                        sender=lambda *_: sent.append(1))
    assert result.status in {"failed", "blocked"} and not sent


def test_daily_unknown_delivery_never_retries_automatically(environment, monkeypatch, tmp_path):
    db, client, _ = environment
    configure_daily(monkeypatch, tmp_path, client)
    job = tick_daily(db, now=START)
    complete_queued(db, job)
    attempts = []

    def sender(*args):
        attempts.append(1)
        return "unknown", "lost_after_data"

    assert tick_daily(db, now=START, sender=sender).status == "needs_review"
    tick_daily(db, now=START + timedelta(minutes=10), sender=sender)
    assert len(attempts) == 1
    from app.api.briefing import DeliveryDecision, resolve_delivery

    delivery = db.scalar(select(EmailDelivery).where(EmailDelivery.report_id == job.report_id))
    resolve_delivery(delivery.id, DeliveryDecision(received=True), db)
    db.refresh(job)
    assert job.status == "sent" and db.get(DailyReport, job.report_id).status == "sent"


def test_daily_waits_for_sampling_and_can_archive_without_smtp(environment, monkeypatch, tmp_path):
    db, client, _ = environment
    configure_daily(monkeypatch, tmp_path, client)
    busy = queue_analysis(db, request("busy", START))
    assert tick_daily(db, now=START) is None
    assert not list(db.scalars(select(DailyBriefingJob)))
    execute_analysis(db, HotspotAnalysisInput.model_validate(busy.input_json))
    job = tick_daily(db, now=START)
    complete_queued(db, job)
    monkeypatch.setattr(settings, "smtp_host", None)
    assert tick_daily(db, now=START).status == "awaiting_email"
    assert db.get(DailyReport, job.report_id).status == "approved"
    assert len(list((tmp_path / "artifacts").rglob("*.html"))) == 1


def test_trial_deadline_stops_new_automatic_jobs(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.init_db import init_db

    engine = create_engine(f"sqlite:///{tmp_path / 'deadline.db'}")
    init_db(engine)
    factory = sessionmaker(engine)
    monkeypatch.setattr(settings, "scheduler_enabled", True)
    monkeypatch.setattr(settings, "daily_briefing_enabled", True)
    monkeypatch.setattr(settings, "email_send_time", "00:00")
    monkeypatch.setattr(settings, "scheduler_until", START)
    coordinator = JobCoordinator(factory)
    coordinator.tick(now=START)
    with factory() as db:
        assert db.scalar(select(AnalysisRun.run_id)) is None
        assert db.scalar(select(DailyBriefingJob.run_id)) is None
    coordinator.stop()
    engine.dispose()


def test_timeout_closes_only_child_tasks_of_that_run(environment):
    db, _, _ = environment
    for plan, run in [("affected", "run-a"), ("other", "run-b")]:
        db.add(AgentTask(plan_id=plan, task_type="fetch", status="succeeded",
                         input_json={"run_id": run}))
        task = AgentTask(plan_id=plan, task_type="classify", status="running", input_json={})
        db.add(task)
        db.flush()
        db.add(AgentToolCall(task_id=task.id, tool_name="classify", status="running"))
    db.commit()
    finish_interrupted_tasks(db, "run-a", "wall_clock_timeout")
    for row in db.scalars(select(AgentTask).where(AgentTask.task_type == "classify")):
        assert row.status == ("failed" if row.plan_id == "affected" else "running")
        assert (row.finished_at is not None) == (row.plan_id == "affected")
        call = db.scalar(select(AgentToolCall).where(AgentToolCall.task_id == row.id))
        assert call.status == row.status


def test_logs_rotate_at_local_midnight_append_and_redact(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret-for-test")
    handler = DailyLogHandler(tmp_path, retention=2)
    for instant in (datetime(2026, 10, 1, 15, 59, tzinfo=UTC),
                    datetime(2026, 10, 1, 16, tzinfo=UTC)):
        record = logging.LogRecord("app.test", logging.INFO, "", 1,
                                   "run=abc secret-for-test", (), None)
        record.created = instant.timestamp()
        handler.handle(record)
    paths = sorted(tmp_path.rglob("*.jsonl"))
    assert [path.parent.name for path in paths] == ["2026-10-01", "2026-10-02"]
    assert "secret-for-test" not in paths[-1].read_text(encoding="utf-8")
    DailyLogHandler(tmp_path).handle(record)
    assert len(paths[-1].read_text(encoding="utf-8").splitlines()) == 2
    assert json.loads(paths[0].read_text(encoding="utf-8"))["time"].endswith("+08:00")
    handler.prune(datetime(2026, 10, 3).date())
    assert not paths[0].exists() and paths[1].exists()


def test_persistent_cache_keys_exact_text_and_model_identity(tmp_path):
    model_root = tmp_path / "models"
    model_root.mkdir()
    manifest = model_root / "manifest.json"
    manifest.write_text('{"revision":"one"}', encoding="utf-8")
    path = tmp_path / "scores.db"
    pair = ("event title", "source title")
    RerankCache(path, model_root).put_many([pair], [0.88])
    assert RerankCache(path, model_root).get_many([pair]) == {pair: 0.88}
    assert RerankCache(path, model_root).get_many([(pair[1], pair[0])]) == {}
    manifest.write_text('{"revision":"two"}', encoding="utf-8")
    assert RerankCache(path, model_root).get_many([pair]) == {}
