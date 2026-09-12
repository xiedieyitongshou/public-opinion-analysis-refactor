"""Agent runtime persistence models.

These tables cover Day 9 only: task tracking, tool-call logs, human review,
guardrail violations, and evaluation bookkeeping. Business data models are
introduced separately in Day 10.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
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


class AgentTask(TimestampMixin, Base):
    __tablename__ = "agent_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    plan_id: Mapped[str | None] = mapped_column(String(120), index=True)
    parent_task_id: Mapped[int | None] = mapped_column(ForeignKey("agent_tasks.id"))
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    parent_task: Mapped["AgentTask | None"] = relationship(remote_side=[id])
    tool_calls: Mapped[list["AgentToolCall"]] = relationship(back_populates="task")


class AgentToolCall(TimestampMixin, Base):
    __tablename__ = "agent_tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("agent_tasks.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    has_side_effect: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    task: Mapped[AgentTask | None] = relationship(back_populates="tool_calls")


class HumanReviewTask(TimestampMixin, Base):
    __tablename__ = "human_review_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    agent_task_id: Mapped[int | None] = mapped_column(ForeignKey("agent_tasks.id"), index=True)
    review_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reviewer: Mapped[str | None] = mapped_column(String(120))
    decision: Mapped[str | None] = mapped_column(String(80))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GuardrailViolation(TimestampMixin, Base):
    __tablename__ = "guardrail_violations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    agent_task_id: Mapped[int | None] = mapped_column(ForeignKey("agent_tasks.id"), index=True)
    tool_call_id: Mapped[int | None] = mapped_column(ForeignKey("agent_tool_calls.id"), index=True)
    rule_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    rule_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(80), index=True)
    subject_id: Mapped[str | None] = mapped_column(String(120), index=True)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CrawlValidationRun(TimestampMixin, Base):
    __tablename__ = "crawl_validation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    plan_id: Mapped[str | None] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    requested_source_ids_json: Mapped[list[str] | None] = mapped_column(JSON)
    skipped_source_ids_json: Mapped[list[str] | None] = mapped_column(JSON)
    unavailable_source_ids_json: Mapped[list[str] | None] = mapped_column(JSON)
    promote_to_event_pool: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_results_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    raw_sample_manifest_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)


class EvaluationRun(TimestampMixin, Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    suite_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="pending", index=True)
    metrics_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationCase(TimestampMixin, Base):
    __tablename__ = "evaluation_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    suite_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    case_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expected_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    tags_json: Mapped[list[str] | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    last_score: Mapped[float | None] = mapped_column(Float)
    last_run_id: Mapped[int | None] = mapped_column(ForeignKey("evaluation_runs.id"), index=True)
