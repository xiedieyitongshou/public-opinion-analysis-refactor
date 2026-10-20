"""Durable, manually started eight-hour trial; never approves or sends reports."""

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from uuid import uuid4

from sqlalchemy import select

from app.models import Item
from app.models.briefing import AnalysisRun, OvernightTest
from app.schemas.collection_session import CollectionSessionConfig
from app.schemas.platform_trend import PlatformTrendConfig
from app.services.briefing import as_utc

ACTIVE = {"running", "stopping"}
logger = logging.getLogger(__name__)
_last_resource_log = 0.0


def record_resources(db):
    global _last_resource_log
    trial = current(db)
    if not trial or trial.status not in ACTIVE or monotonic() - _last_resource_log < 60:
        return
    metrics = {}
    for name in ("memory.current", "memory.peak", "memory.events", "cpu.stat"):
        path = Path("/sys/fs/cgroup") / name
        if path.exists():
            metrics[name] = path.read_text().strip()
    logger.info("overnight_resources token=%s metrics=%s", trial.token, json.dumps(metrics))
    _last_resource_log = monotonic()


def preflight(db):
    from app.core.config import settings
    from app.services.request_usage import request_scope
    from app.services.weibo_heat_client import WeiboHeatClient

    if not settings.weibo_cli_enabled:
        raise ValueError("请先启用微博 CLI，夜间测试需要验证完整增强链路")
    if settings.matching_profile != "hybrid_rerank":
        raise ValueError("夜间完整链路测试需要 MATCHING_PROFILE=hybrid_rerank")
    # Container headroom is observable here; Windows host free RAM is not.
    limit = Path("/sys/fs/cgroup/memory.max")
    usage = Path("/sys/fs/cgroup/memory.current")
    if limit.exists() and usage.exists() and limit.read_text().strip().isdigit():
        if int(limit.read_text()) - int(usage.read_text()) < 1536 * 1024**2:
            raise ValueError("后端容器可用内存配额不足 1.5 GiB，请释放资源后重试")
    title = db.scalar(
        select(Item.title)
        .where(Item.signal_role == "topic_discovery_signal")
        .order_by(Item.id.desc())
    )
    with request_scope("overnight-preflight", settings.request_limits, 90) as scope:
        try:
            result = WeiboHeatClient().search_with_cli(title or "微博热搜")
        finally:
            scope.flush(db)
    if not result.available:
        reason = (
            "出口地区受限，请让 Docker 请求走国内直连"
            if "IP_GEO_DENIED" in (result.stderr or "")
            else "请检查 CLI 授权、网络或额度"
        )
        raise ValueError("微博 CLI 启动检查失败：" + reason)


def current(db):
    return db.get(OvernightTest, 1)


def describe(db):
    row = current(db)
    if row is None:
        return {"status": "idle", "rounds": []}
    rounds = []
    for entry in row.rounds_json:
        run = db.get(AnalysisRun, entry.get("run_id")) if entry.get("run_id") else None
        rounds.append(
            {
                **entry,
                "status": run.status if run else "skipped",
                "error": run.error if run else entry.get("error"),
                "report_id": run.report_id if run else None,
            }
        )
    config = CollectionSessionConfig.model_validate(row.config_json or {})
    summary = db.get(AnalysisRun, f"overnight-{row.token}-summary")
    report_id = summary.report_id if summary else row.report_id
    return dict(
        status=row.status,
        started_at=row.started_at,
        ends_at=row.ends_at,
        rounds=rounds,
        report_id=report_id,
        error=row.error,
        planned_rounds=(config.duration_hours * 60 // config.interval_minutes + 1)
        if config.duration_hours
        else None,
        interval_minutes=config.interval_minutes,
        config=config.model_dump(),
        email_sent=False,
    )


def start(db, now=None, config=None):
    config = config or CollectionSessionConfig()
    from app.core.config import settings
    from app.services.briefing_jobs import acquire_lease, release_lease
    from app.services.email_settings import get_email_settings

    now = now or datetime.now(UTC)
    owner = "overnight-start:" + str(uuid4())
    if not acquire_lease(db, "overnight-start", owner, seconds=180, now=now):
        raise ValueError("已有启动请求，请稍后刷新")
    try:
        row = current(db)
        if row and row.status in ACTIVE:
            return row
        if settings.scheduler_enabled:
            raise ValueError("请先关闭常规定时采集 SCHEDULER_ENABLED，避免有限测试结束后继续采集")
        if db.scalar(select(AnalysisRun).where(AnalysisRun.status.in_(["queued", "running"]))):
            raise ValueError("请等待当前采集完成后启动夜间测试")
        mail = get_email_settings(db, decrypt=False)
        if mail.email_schedule_enabled or mail.daily_briefing_enabled:
            raise ValueError("请先关闭邮件设置中的自动日报与定时发送，夜间测试仅生成本地草稿")
        preflight(db)
        if db.scalar(select(AnalysisRun).where(AnalysisRun.status.in_(["queued", "running"]))):
            raise ValueError("启动检查期间有其他采集启动，请等待完成后重试")
        if row is None:
            row = OvernightTest(id=1)
            db.add(row)
        row.token, row.status = uuid4().hex[:16], "running"
        row.started_at = now
        row.ends_at = now + timedelta(hours=config.duration_hours or 87600)
        row.config_json = config.model_dump(mode="json")
        row.rounds_json, row.report_id, row.error = [], None, None
        db.commit()
        logger.info("overnight_started token=%s ends=%s", row.token, row.ends_at)
        return row
    finally:
        release_lease(db, "overnight-start", owner)


def stop(db):
    row = current(db)
    if row and row.status == "running":
        row.status = "stopping"
        db.commit()
    return describe(db)


def is_trial_run(db, run_id):
    row = current(db)
    return bool(row and any(s.get("run_id") == run_id for s in row.rounds_json))


def tick(db, now):
    """Called under the existing coordinator lease. Missed hours are not backfilled."""
    from app.services.briefing_jobs import default_analysis_input, queue_analysis

    row = current(db)
    if not row or row.status not in ACTIVE:
        return False
    busy = db.scalar(select(AnalysisRun).where(AnalysisRun.status.in_(["queued", "running"])))
    if busy:
        return True
    config = CollectionSessionConfig.model_validate(row.config_json or {})
    planned = (
        config.duration_hours * 60 // config.interval_minutes + 1 if config.duration_hours else None
    )
    slots = list(row.rounds_json)
    hour = int((now - as_utc(row.started_at)).total_seconds() // (config.interval_minutes * 60))
    if row.status == "running" and (planned is None or len(slots) < planned):
        # Resume only the present hour after downtime, never burst several paid rounds.
        while len(slots) < (min(hour, planned) if planned is not None else hour):
            slots.append(dict(slot=len(slots), error="missed_hour_after_delay"))
        if len(slots) <= hour and (planned is None or len(slots) < planned):
            slot = len(slots)
            run_id = f"overnight-{row.token}-{slot}"
            data = default_analysis_input(run_id)
            # Keep shared daily RSS sources, including official news.
            # Respect configured official search switch and existing per-round budgets.
            data.trend_configs = {
                p: PlatformTrendConfig(expected_interval_minutes=config.interval_minutes)
                for p in ("zhihu", "weibo")
            }
            queued = queue_analysis(db, data)
            if queued.run_id != run_id:
                return True
            slots.append(dict(slot=slot, run_id=run_id))
            row.rounds_json = slots
            db.commit()
            logger.info("overnight_round_queued token=%s slot=%s run=%s", row.token, slot, run_id)
            return True
        row.rounds_json = slots
        db.commit()
    if row.status == "stopping" or (
        planned is not None and len(slots) == planned and now >= as_utc(row.ends_at)
    ):
        try:
            row.report_id = summarize(db, row, now) if config.generate_report else None
            row.status = "stopped" if row.status == "stopping" else "completed"
            row.error = (
                None if row.report_id or not config.generate_report else "没有可用轮次，未生成日报"
            )
        except Exception as exc:
            logger.exception("overnight_summary_failed")
            db.rollback()
            row = current(db)
            row.status, row.error = "failed", f"生成汇总失败：{type(exc).__name__}"
        db.commit()
        logger.info(
            "overnight_finished token=%s status=%s report=%s", row.token, row.status, row.report_id
        )
    return True


def summarize(db, trial, now):
    from app.services.report_window import build_window

    config = CollectionSessionConfig.model_validate(trial.config_json or {})
    run_ids = [s["run_id"] for s in trial.rounds_json if s.get("run_id")]
    usable = [db.get(AnalysisRun, key) for key in run_ids]
    if not any(r and r.output_json and r.status in ("succeeded", "partial") for r in usable):
        return None
    start = (
        (as_utc(trial.started_at) - timedelta(microseconds=1))
        if config.report_scope == "session"
        else now - timedelta(hours=24)
    )
    return build_window(
        db,
        start=start,
        end=now,
        run_ids=run_ids if config.report_scope == "session" else None,
        interval_minutes=config.interval_minutes,
        run_id=f"overnight-{trial.token}-summary",
        label="本次运行" if config.report_scope == "session" else "过去24小时",
    )
