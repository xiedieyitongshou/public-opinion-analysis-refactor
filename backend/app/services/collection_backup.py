"""Verified SQLite backup before a transactional fresh collection workspace."""

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.db.session import Base
from app.models.briefing import AnalysisRun, EmailDelivery, OvernightTest

# Configuration, request accounting, quota and evaluation records are retained.
RESET_TABLES = {
    "overnight_tests", "analysis_runs", "report_revisions", "email_deliveries",
    "daily_dispatches", "daily_email_audiences", "daily_briefing_jobs",
    "human_review_tasks", "guardrail_violations", "agent_tool_calls", "agent_tasks",
    "items", "event_snapshots", "platform_scores", "event_queries", "events", "daily_reports",
}


def backup_and_clear(db):
    """Leave clearing uncommitted so the caller can atomically create the new session.

    BEGIN IMMEDIATE prevents writers changing the source between backup and reset.
    A separate read connection uses SQLite's backup API, including committed WAL data.
    """
    bind = db.get_bind()
    filename = bind.url.database
    if bind.dialect.name != "sqlite" or not filename or filename == ":memory:":
        raise ValueError("独立采集目前只支持文件型 SQLite 数据库")
    source = Path(filename).resolve()
    directory = source.parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    name = datetime.now(UTC).strftime("before-fresh-%Y%m%dT%H%M%SZ-") + uuid4().hex[:8] + ".db"
    target = directory / name
    partial = directory / (name + ".partial")
    db.rollback()
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    try:
        active = db.scalar(select(AnalysisRun).where(
            AnalysisRun.status.in_(["queued", "running"])
        ))
        trial = db.get(OvernightTest, 1)
        sending = db.scalar(select(EmailDelivery).where(
            EmailDelivery.status.in_(["sending", "pending"])
        ))
        if active or sending or (trial and trial.status in {"running", "stopping"}):
            raise ValueError("请等待采集、任务或邮件发送完成后再备份并独立采集")
        # Exclusive creation and restrictive permissions: backups contain private settings.
        descriptor = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
            with closing(sqlite3.connect(partial)) as writer:
                reader.backup(writer)
                if writer.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("数据库备份校验失败，原数据未清理")
        partial.rename(target)
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in RESET_TABLES:
                db.execute(table.delete())
        db.expunge_all()
        return name
    except Exception:
        db.rollback()
        raise
