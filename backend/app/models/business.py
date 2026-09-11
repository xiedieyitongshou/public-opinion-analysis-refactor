"""Business data persistence models for public opinion analysis."""

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Source(TimestampMixin, Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    source_status: Mapped[str] = mapped_column(
        String(60), default="use", nullable=False, index=True
    )
    source_origin: Mapped[str | None] = mapped_column(String(60), index=True)
    platform: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    base_url: Mapped[str | None] = mapped_column(String(500))
    credibility_weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    fetch_interval_minutes: Mapped[int | None] = mapped_column(Integer)
    fetch_limit: Mapped[int | None] = mapped_column(Integer)
    fetch_config_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    items: Mapped[list["Item"]] = relationship(back_populates="source")


class Item(TimestampMixin, Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    url: Mapped[str | None] = mapped_column(String(1000))
    source_status: Mapped[str] = mapped_column(
        String(60), default="use", nullable=False, index=True
    )
    source_origin: Mapped[str | None] = mapped_column(String(60), index=True)
    signal_role: Mapped[str | None] = mapped_column(String(80), index=True)
    author: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    language: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    raw_payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    raw_metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    normalized_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    quality_flags_json: Mapped[list[str] | None] = mapped_column(JSON)
    signal_contribution_roles_json: Mapped[list[str] | None] = mapped_column(JSON)
    source_citation_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    source: Mapped[Source] = relationship(back_populates="items")
    event: Mapped["Event | None"] = relationship(back_populates="items")


class Event(TimestampMixin, Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str | None] = mapped_column(String(80), index=True)
    lifecycle_status: Mapped[str] = mapped_column(
        String(40),
        default="active",
        nullable=False,
        index=True,
    )
    primary_entity: Mapped[str | None] = mapped_column(String(255), index=True)
    keywords_json: Mapped[list[str] | None] = mapped_column(JSON)
    event_fingerprint: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    confidence_score: Mapped[float | None] = mapped_column(Float)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    source_citations_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    event_detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    items: Mapped[list[Item]] = relationship(back_populates="event")
    platform_scores: Mapped[list["PlatformScore"]] = relationship(back_populates="event")
    snapshots: Mapped[list["EventSnapshot"]] = relationship(back_populates="event")


class PlatformScore(TimestampMixin, Base):
    __tablename__ = "platform_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    raw_score: Mapped[float | None] = mapped_column(Float)
    normalized_score: Mapped[float | None] = mapped_column(Float)
    score_detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    event: Mapped[Event] = relationship(back_populates="platform_scores")


class EventSnapshot(TimestampMixin, Base):
    __tablename__ = "event_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    official_score: Mapped[float | None] = mapped_column(Float)
    bbs_score: Mapped[float | None] = mapped_column(Float)
    velocity_score: Mapped[float | None] = mapped_column(Float)
    coverage_score: Mapped[float | None] = mapped_column(Float)
    controversy_score: Mapped[float | None] = mapped_column(Float)
    total_score: Mapped[float | None] = mapped_column(Float, index=True)
    trend_status: Mapped[str | None] = mapped_column(String(60), index=True)
    metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    is_daily_compacted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )

    event: Mapped[Event] = relationship(back_populates="snapshots")


class DailyReport(TimestampMixin, Base):
    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(60), default="draft", nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    briefing_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source_citations_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    quality_review_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    guardrail_summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    image_path: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class EventQuery(TimestampMixin, Base):
    __tablename__ = "event_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    query_text: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    normalized_query: Mapped[str | None] = mapped_column(String(500), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False, index=True)
    filters_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    retrieved_evidence_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    searched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
