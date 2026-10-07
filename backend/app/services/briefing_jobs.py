"""Durable queue + a single coordinator. Workers have an enforced wall-clock deadline."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import AgentTask
from app.models.briefing import AnalysisRun, JobLease, QuotaSnapshot, RequestLog
from app.schemas.analysis import HotspotAnalysisInput
from app.schemas.collectors import FetchSourceItemsInput
from app.schemas.platform_trend import PlatformTrendConfig
from app.services.briefing import as_utc
from app.services.request_usage import request_scope


def acquire_lease(db, name, owner, *, seconds=60, now=None):
    now = now or datetime.now(UTC)
    result = db.execute(
        update(JobLease)
        .where(
            JobLease.name == name,
            or_(JobLease.expires_at < now, JobLease.owner == owner),
        )
        .values(owner=owner, expires_at=now + timedelta(seconds=seconds))
    )
    if result.rowcount:
        db.commit()
        return True
    try:
        db.add(JobLease(name=name, owner=owner, expires_at=now + timedelta(seconds=seconds)))
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def release_lease(db, name, owner):
    db.execute(delete(JobLease).where(JobLease.name == name, JobLease.owner == owner))
    db.commit()


def default_analysis_input(run_id=None):
    from app.agents.planning import Planner

    sources = list(Planner.daily_collection_source_ids)
    sources.insert(1, "zhihu_search")
    return HotspotAnalysisInput(
        run_id=run_id or str(uuid4()),
        matching_profile=settings.matching_profile,
        official_search_enabled=settings.official_search_enabled,
        collection=FetchSourceItemsInput(
            source_ids=sources,
            validate_only=False,
            dry_run=False,
            per_source_params={"zhihu_search": {"from_hotlist": True, "max_queries": 3}},
            per_source_limits={"zhihu_search": 5},
        ),
        trend_configs={
            platform: PlatformTrendConfig(
                expected_interval_minutes=settings.sampling_interval_minutes,
            )
            for platform in ("zhihu", "weibo")
        },
    )


def queue_analysis(db, data=None):
    data = data or default_analysis_input()
    existing = db.get(AnalysisRun, data.run_id)
    if existing:
        if existing.input_json != data.model_dump(mode="json"):
            raise ValueError("同一轮次不能改变输入")
        return existing
    busy = db.scalar(select(AnalysisRun).where(AnalysisRun.status.in_(["queued", "running"])))
    if busy:
        return busy
    row = AnalysisRun(
        run_id=data.run_id,
        status="queued",
        started_at=datetime.now(UTC),
        input_json=data.model_dump(mode="json"),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.get(AnalysisRun, data.run_id) or db.scalar(
            select(AnalysisRun).where(AnalysisRun.status.in_(["queued", "running"]))
        )
        if existing is None:
            raise
        return existing
    return row


def capture_collection(db, output):
    task = db.scalar(
        select(AgentTask)
        .where(
            AgentTask.plan_id == output.plan_id,
            AgentTask.task_type == "fetch_source_items",
        )
        .order_by(AgentTask.id.desc())
    )
    return task.output_json if task and task.output_json else {}


def probe_quota(db):
    from app.services.zhihu_client import ZhihuClient

    status, values = "unknown", {}
    try:
        result = ZhihuClient().fetch_quota()
        # Keep the API's actual quota fields, never invent a currency conversion.
        raw = result.raw_payload
        data = raw.get("data", raw.get("Data", {}))
        if isinstance(data, (dict, list)):
            values = {"api_data": data}
            status = "available" if data else "unknown"
    except Exception as exc:
        values = {"error": type(exc).__name__}
    db.add(
        QuotaSnapshot(
            source_id="zhihu",
            observed_at=datetime.now(UTC),
            provenance="Zhihu /api/v1/quota",
            status=status,
            values_json=values,
        )
    )
    db.commit()


def execute_analysis(db, data, *, agent=None, sink=None):
    from app.agents.analysis import HotspotAnalysisAgent

    row = db.get(AnalysisRun, data.run_id) or queue_analysis(db, data)
    if row.run_id != data.run_id:
        raise ValueError("已有采集任务在运行")
    if row.input_json != data.model_dump(mode="json"):
        raise ValueError("同一轮次不能改变输入")
    if row.status not in {"queued"}:
        return row  # Completed/failed run IDs are immutable; replay adds no requests.
    owner = f"analysis:{data.run_id}:{uuid4()}"
    if not acquire_lease(db, "analysis", owner, seconds=settings.job_timeout_seconds + 60):
        return row
    row.status, row.started_at = "running", datetime.now(UTC)
    db.commit()
    try:
        with request_scope(
            data.run_id, settings.request_limits, settings.job_timeout_seconds, sink=sink
        ) as scope:
            try:
                if settings.quota_probe_enabled:
                    probe_quota(db)
                output = (agent or HotspotAnalysisAgent()).run(db, data)
                final_status = output.status
                row.output_json = output.model_dump(mode="json")
                row.window_end = output.window_end
                row.collection_json = capture_collection(db, output)
                row.error = "; ".join(output.errors)[:1000] or None
                db.commit()
                if output.window_end and output.status != "failed":
                    from app.agents.planning import Planner
                    from app.agents.runner import AgentTaskRunner
                    from app.tools import default_tool_registry

                    tasks = AgentTaskRunner(default_tool_registry).run_plan(
                        Planner().create_daily_briefing_plan({"run_id": data.run_id}),
                        db,
                    )
                    last = tasks[-1]
                    if last.status == "succeeded" and last.task_type == "save_daily_report":
                        row.report_id = last.output_json["report_id"]
                    else:
                        row.error = last.error_message or "日报生成失败，请查看工具日志"
                        final_status = "partial"
                # Keep the active-job constraint until report generation has
                # finished; CLI/coordinator observers must never return early.
                row.status = final_status
                row.finished_at = datetime.now(UTC)
                db.commit()
            finally:
                db.rollback()
                scope.flush(db)
    except Exception as exc:
        db.rollback()
        row = db.get(AnalysisRun, data.run_id)
        row.status, row.error = "failed", f"{type(exc).__name__}: {str(exc)[:500]}"
        row.finished_at = datetime.now(UTC)
        db.commit()
    finally:
        release_lease(db, "analysis", owner)
    return row


def run_worker(run_id):
    def sink(value):
        with SessionLocal() as usage_db:
            usage_db.add(RequestLog(**value))
            usage_db.commit()

    with SessionLocal() as db:
        row = db.get(AnalysisRun, run_id)
        if row:
            execute_analysis(db, HotspotAnalysisInput.model_validate(row.input_json), sink=sink)


class JobCoordinator:
    """One background coordinator per database; no in-process runaway analysis threads."""

    def __init__(self, factory=SessionLocal):
        self.factory, self.owner = factory, str(uuid4())
        self.stop_event = threading.Event()
        self.thread = None
        self.process = None
        self.process_run_id = None
        self.process_started = None
        self.last_error = None

    def start(self):
        self.thread = threading.Thread(target=self._loop, name="briefing-scheduler", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
            with self.factory() as db:
                self._fail_run(db, self.process_run_id, "service_stopped")
        with self.factory() as db:
            release_lease(db, "coordinator", self.owner)

    def _fail_run(self, db, run_id, reason):
        row = db.get(AnalysisRun, run_id)
        if row and row.status in {"queued", "running"}:
            row.status, row.error, row.finished_at = "failed", reason, datetime.now(UTC)
            db.commit()
        lease = db.get(JobLease, "analysis")
        if lease and lease.owner.startswith(f"analysis:{run_id}:"):
            db.execute(
                delete(JobLease).where(
                    JobLease.name == "analysis",
                    JobLease.owner == lease.owner,
                )
            )
        db.commit()

    def tick(self, now=None):
        now = now or datetime.now(UTC)
        with self.factory() as db:
            if not acquire_lease(db, "coordinator", self.owner, seconds=120, now=now):
                return
            if self.process:
                if self.process.poll() is None:
                    if monotonic() - self.process_started <= settings.job_timeout_seconds:
                        return
                    self.process.terminate()
                    self.process.wait(timeout=10)
                    self._fail_run(db, self.process_run_id, "wall_clock_timeout")
                else:
                    self._fail_run(db, self.process_run_id, "worker_exited_before_completion")
                self.process = None
            stale = db.scalars(
                select(AnalysisRun).where(
                    AnalysisRun.status == "running",
                    AnalysisRun.started_at
                    < now - timedelta(seconds=settings.job_timeout_seconds + 60),
                )
            ).all()
            for row in stale:
                self._fail_run(db, row.run_id, "interrupted_worker")
            if settings.scheduler_enabled:
                latest = db.scalar(select(AnalysisRun).order_by(AnalysisRun.started_at.desc()))
                if latest is None or now - as_utc(latest.started_at) >= timedelta(
                    minutes=settings.sampling_interval_minutes
                ):
                    queue_analysis(db)
            # Send only today's explicitly approved report; this call never approves a draft.
            if settings.email_schedule_enabled:
                from app.services.briefing_email import dispatch_daily

                dispatch_daily(db, now=now)
            lease = db.get(JobLease, "analysis")
            if lease and as_utc(lease.expires_at) > now:
                return
            queued = db.scalar(
                select(AnalysisRun)
                .where(AnalysisRun.status == "queued")
                .order_by(AnalysisRun.started_at)
            )
            if queued:
                self.process_run_id, self.process_started = queued.run_id, monotonic()
                self.process = subprocess.Popen(
                    [sys.executable, "-m", "app.services.briefing_jobs", queued.run_id],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
                self.last_error = None
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            self.stop_event.wait(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    run_worker(parser.parse_args().run_id)
