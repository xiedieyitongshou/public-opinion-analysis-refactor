"""Planning schemas and minimal planner templates."""

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class PlanStep(BaseModel):
    step_id: str
    name: str
    tool_name: str
    input_json: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    mode: Literal["daily_briefing", "event_search"]
    steps: list[PlanStep]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_step_dependencies(self) -> "Plan":
        seen: set[str] = set()
        for step in self.steps:
            if step.step_id in seen:
                raise ValueError(f"Duplicate step_id: {step.step_id}")
            missing_dependencies = set(step.depends_on) - seen
            if missing_dependencies:
                missing = ", ".join(sorted(missing_dependencies))
                raise ValueError(
                    f"Step {step.step_id} depends on unknown or later steps: {missing}"
                )
            seen.add(step.step_id)
        return self


class TaskGraph(BaseModel):
    plan: Plan

    def execution_order(self) -> list[PlanStep]:
        """Return linear execution order.

        Day 12 intentionally supports only linear plans. Dependency validation
        still exists so later DAG support has a clean migration path.
        """
        return self.plan.steps


class Planner:
    def create_daily_briefing_plan(self, payload: dict[str, Any] | None = None) -> Plan:
        payload = payload or {}
        steps = [
            PlanStep(
                step_id="fetch_source_items",
                name="Fetch source items",
                tool_name="fetch_source_items",
                input_json=payload,
            ),
            PlanStep(
                step_id="normalize_raw_items",
                name="Normalize raw items",
                tool_name="normalize_raw_items",
                depends_on=["fetch_source_items"],
            ),
            PlanStep(
                step_id="extract_event_signals",
                name="Extract event signals",
                tool_name="extract_event_signals",
                depends_on=["normalize_raw_items"],
            ),
            PlanStep(
                step_id="match_and_resolve_events",
                name="Match and resolve events",
                tool_name="match_and_resolve_events",
                depends_on=["extract_event_signals"],
            ),
            PlanStep(
                step_id="calculate_event_scores",
                name="Calculate event scores",
                tool_name="calculate_event_scores",
                depends_on=["match_and_resolve_events"],
            ),
            PlanStep(
                step_id="generate_daily_briefing",
                name="Generate daily briefing",
                tool_name="generate_daily_briefing",
                depends_on=["calculate_event_scores"],
            ),
            PlanStep(
                step_id="review_briefing_quality",
                name="Review briefing quality",
                tool_name="review_briefing_quality",
                depends_on=["generate_daily_briefing"],
            ),
            PlanStep(
                step_id="run_briefing_guardrails",
                name="Run briefing guardrails",
                tool_name="run_briefing_guardrails",
                depends_on=["review_briefing_quality"],
            ),
            PlanStep(
                step_id="create_human_review_task",
                name="Create human review task",
                tool_name="create_human_review_task",
                depends_on=["run_briefing_guardrails"],
            ),
        ]
        return Plan(name="Daily briefing workflow", mode="daily_briefing", steps=steps)

    def create_event_search_plan(self, query_text: str) -> Plan:
        return Plan(
            name="Event search placeholder workflow",
            mode="event_search",
            steps=[
                PlanStep(
                    step_id="search_existing_evidence",
                    name="Search existing evidence",
                    tool_name="search_existing_evidence",
                    input_json={"query_text": query_text},
                )
            ],
            metadata={"query_text": query_text},
        )
