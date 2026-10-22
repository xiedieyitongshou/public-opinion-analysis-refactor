"""Persisted collection settings and explicit report windows."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CollectionSessionConfig(BaseModel):
    duration_hours: int | None = Field(default=8, ge=1, le=168)
    interval_minutes: int = Field(default=60, ge=30, le=180)
    report_scope: Literal["session", "last24h"] = "session"
    generate_report: bool = True
    fresh_start: bool = False

    @model_validator(mode="after")
    def fresh_session_report(self):
        if self.fresh_start:
            self.report_scope = "session"
        return self


class ReportWindowInput(BaseModel):
    scope: Literal["session", "last24h", "custom"] = "session"
    window_start: datetime | None = None
    window_end: datetime | None = None

    @model_validator(mode="after")
    def valid_window(self):
        for value in (self.window_start, self.window_end):
            if value and value.tzinfo is None:
                raise ValueError("时间必须包含时区")
        if self.scope == "custom":
            if not self.window_start or not self.window_end:
                raise ValueError("自定义范围需要起止时间")
            hours = (self.window_end - self.window_start).total_seconds() / 3600
            if not 0 < hours <= 168:
                raise ValueError("统计窗口须大于零且不超过七天")
        return self
