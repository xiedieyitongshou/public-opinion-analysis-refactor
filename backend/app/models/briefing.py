"""Week 8 durable analysis, request accounting, job locks and delivery receipts."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    run_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    collection_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("daily_reports.id"))


class ReportRevision(Base):
    __tablename__ = "report_revisions"
    report_id: Mapped[int] = mapped_column(ForeignKey("daily_reports.id"), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(100), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("daily_reports.id"))
    approval_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class RequestLog(Base):
    __tablename__ = "request_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(String(100), index=True)
    source_id: Mapped[str] = mapped_column(String(100), index=True)
    operation: Mapped[str] = mapped_column(String(100))
    unit: Mapped[str] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    response_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(100))
    cost: Mapped[float | None] = mapped_column(Float)


class QuotaSnapshot(Base):
    __tablename__ = "quota_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(String(100), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provenance: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30))
    values_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class JobLease(Base):
    __tablename__ = "job_leases"
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    owner: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EmailDelivery(Base):
    __tablename__ = "email_deliveries"
    __table_args__ = (UniqueConstraint("report_id", "recipient", name="uq_report_recipient"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("daily_reports.id"), index=True)
    recipient: Mapped[str] = mapped_column(String(320))
    message_id: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class DailyDispatch(Base):
    __tablename__ = "daily_dispatches"
    local_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("daily_reports.id"))
