"""Durable validated pair decisions, independent of the short-lived HTTP cache."""

from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.agent_runtime import TimestampMixin


class EventPairJudgment(TimestampMixin, Base):
    __tablename__ = "event_pair_judgments"

    material_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_a: Mapped[str] = mapped_column(String(120), index=True)
    event_b: Mapped[str] = mapped_column(String(120), index=True)
    decision_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    audit_json: Mapped[dict[str, Any]] = mapped_column(JSON)
