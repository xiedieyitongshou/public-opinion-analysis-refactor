"""Guardrail rule registry and persistence helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import GuardrailViolation
from app.schemas import (
    GuardrailCheckInput,
    GuardrailCheckResult,
    GuardrailRuleInfo,
    GuardrailRuleType,
    GuardrailRunResult,
)

GuardrailHandler = Callable[[GuardrailCheckInput], GuardrailCheckResult]


class GuardrailRegistryError(RuntimeError):
    """Base error for guardrail registry failures."""


class GuardrailAlreadyRegisteredError(GuardrailRegistryError):
    """Raised when a rule is registered more than once."""


@dataclass(frozen=True)
class GuardrailRule:
    name: str
    rule_type: GuardrailRuleType
    description: str
    handler: GuardrailHandler

    def info(self) -> GuardrailRuleInfo:
        return GuardrailRuleInfo(
            name=self.name,
            rule_type=self.rule_type,
            description=self.description,
        )


class GuardrailRegistry:
    def __init__(self) -> None:
        self._rules: dict[str, GuardrailRule] = {}

    def register(self, rule: GuardrailRule) -> None:
        if rule.name in self._rules:
            raise GuardrailAlreadyRegisteredError(f"Guardrail already registered: {rule.name}")
        self._rules[rule.name] = rule

    def list_rules(self) -> list[GuardrailRuleInfo]:
        return [rule.info() for rule in self._rules.values()]

    def run(
        self,
        input_data: GuardrailCheckInput,
        *,
        rule_types: set[GuardrailRuleType] | None = None,
        db: Session | None = None,
    ) -> GuardrailRunResult:
        checks: list[GuardrailCheckResult] = []
        for rule in self._rules.values():
            if rule_types is not None and rule.rule_type not in rule_types:
                continue
            result = rule.handler(input_data)
            checks.append(result)
            if db is not None and result.severity != "pass":
                persist_guardrail_violation(
                    db,
                    result,
                    agent_task_id=input_data.agent_task_id,
                    tool_call_id=input_data.tool_call_id,
                )

        severities = [check.severity for check in checks]
        status = "block" if "block" in severities else "warn" if "warn" in severities else "pass"
        return GuardrailRunResult(
            status=status,
            checks=checks,
            violations=[
                violation
                for check in checks
                if (violation := check.as_violation()) is not None
            ],
            blocks_publish=any(check.blocks_publish for check in checks),
            review_required=any(check.review_required for check in checks),
        )


def persist_guardrail_violation(
    db: Session,
    result: GuardrailCheckResult,
    *,
    agent_task_id: int | None,
    tool_call_id: int | None,
) -> GuardrailViolation:
    violation = GuardrailViolation(
        agent_task_id=agent_task_id,
        tool_call_id=tool_call_id,
        rule_name=result.rule_name,
        rule_type=result.rule_type,
        severity=result.severity,
        message=result.message,
        subject_type=result.subject_type,
        subject_id=result.subject_id,
        evidence_json=result.evidence,
    )
    db.add(violation)
    db.commit()
    db.refresh(violation)
    return violation
