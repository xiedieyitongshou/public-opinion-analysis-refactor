"""Small operational records, partitioned by local calendar date; never log payloads."""

import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.config import settings


def redact(value):
    text = str(value)
    for secret in (
        settings.admin_token, settings.zhihu_access_secret, settings.deepseek_api_key,
        settings.smtp_password,
        os.getenv("WEIBO_CLI_TOKEN"), os.getenv("WEIBO_CLI_REFRESH_TOKEN"),
    ):
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return re.sub(
        r"(?i)(authorization|password|access_secret|token)([\s=:]+)[^\s,;]+",
        r"\1\2[REDACTED]", text,
    )


class DailyLogHandler(logging.Handler):
    def __init__(self, root, *, name="service", timezone="Asia/Shanghai", retention=14):
        super().__init__()
        self.root = Path(root).resolve()
        self.name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:120]
        self.zone, self.retention = ZoneInfo(timezone), retention
        self.last_day = None

    def emit(self, record):
        try:
            local = datetime.fromtimestamp(record.created, UTC).astimezone(self.zone)
            day = local.date().isoformat()
            folder = self.root / day
            folder.mkdir(parents=True, exist_ok=True)
            if self.last_day != day:
                self.prune(local.date())
                self.last_day = day
            data = {
                "time": local.isoformat(), "level": record.levelname,
                "logger": record.name, "pid": record.process,
                "message": redact(record.getMessage())[:8000],
            }
            # One owner per file (coordinator, or an individual worker); append survives restart.
            with (folder / f"{self.name}.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(data, ensure_ascii=False) + "\n")
        except Exception:
            self.handleError(record)

    def prune(self, today):
        cutoff = (today - timedelta(days=self.retention - 1)).isoformat()
        for folder in self.root.glob("????-??-??"):
            if (not folder.is_dir() or folder.is_symlink()
                    or folder.name >= cutoff or folder.resolve().parent != self.root):
                continue
            for path in folder.glob("*.jsonl"):
                if not path.is_symlink() and path.resolve().parent == folder.resolve():
                    path.unlink(missing_ok=True)
            if not any(folder.iterdir()):
                folder.rmdir()


def configure_logging(name="service"):
    logger = logging.getLogger("app")
    logger.setLevel(logging.INFO)
    if settings.runtime_log_dir and not any(
        isinstance(handler, DailyLogHandler) for handler in logger.handlers
    ):
        logger.addHandler(DailyLogHandler(
            settings.runtime_log_dir, name=name, timezone=settings.briefing_timezone,
            retention=settings.runtime_log_retention_days,
        ))
    return logger
