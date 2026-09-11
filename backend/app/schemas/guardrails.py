"""Guardrail schemas for Day 26 runtime checks."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

GuardrailRuleType = Literal[
    "event_merge",
    "summary",
    "source_quality",
    "tool_output",
    "briefing_publish",
    "classification_quality",
]

GuardrailSeverity = Literal["pass", "warn", "block"]


class GuardrailCheckInput(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    subject_type: str | None = None
    subject_id: str | None = None
    agent_task_id: int | None = None
    tool_call_id: int | None = None


class GuardrailViolationRecord(BaseModel):
    rule_name: str
    rule_type: GuardrailRuleType
    severity: GuardrailSeverity
    message: str
    subject_type: str | None = None
    subject_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class GuardrailCheckResult(BaseModel):
    rule_name: str
    rule_type: GuardrailRuleType
    severity: GuardrailSeverity
    passed: bool
    message: str
    subject_type: str | None = None
    subject_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    review_required: bool = False
    blocks_publish: bool = False

    def as_violation(self) -> GuardrailViolationRecord | None:
        if self.severity == "pass":
            return None
        return GuardrailViolationRecord(
            rule_name=self.rule_name,
            rule_type=self.rule_type,
            severity=self.severity,
            message=self.message,
            subject_type=self.subject_type,
            subject_id=self.subject_id,
            evidence=self.evidence,
        )


class GuardrailRunResult(BaseModel):
    status: GuardrailSeverity
    checks: list[GuardrailCheckResult]
    violations: list[GuardrailViolationRecord] = Field(default_factory=list)
    blocks_publish: bool = False
    review_required: bool = False


class GuardrailRuleInfo(BaseModel):
    name: str
    rule_type: GuardrailRuleType
    description: str
