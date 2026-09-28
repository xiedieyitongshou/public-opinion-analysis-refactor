"""Minimal linear agent task runner."""

from copy import deepcopy
from typing import Any

from sqlalchemy.orm import Session

from app.agents.planning import Plan, TaskGraph
from app.agents.state_machine import TaskStatus, transition_task
from app.models import AgentTask
from app.tools import ToolContext, ToolRegistry, default_tool_registry


class AgentTaskRunner:
    def __init__(self, registry: ToolRegistry | None = None, *, actor: str = "agent") -> None:
        self.registry = registry or default_tool_registry
        self.actor = actor

    def run_plan(self, plan: Plan, db: Session) -> list[AgentTask]:
        tasks: list[AgentTask] = []
        outputs: dict[str, dict[str, Any]] = {}

        for step in TaskGraph(plan=plan).execution_order():
            task = AgentTask(
                task_type=step.tool_name,
                status=TaskStatus.PENDING.value,
                title=step.name,
                plan_id=plan.plan_id,
                input_json=step.input_json,
                max_retries=self.registry.get(step.tool_name).max_retries,
            )
            db.add(task)
            db.commit()
            db.refresh(task)

            transition_task(task, TaskStatus.RUNNING)
            db.add(task)
            db.commit()
            db.refresh(task)

            try:
                resolved_input = deepcopy(step.input_json)
                for field, binding in step.input_bindings.items():
                    parts = binding.split(".")
                    value = outputs[parts[0]]
                    for part in parts[1:]:
                        value = value[part]
                    resolved_input[field] = deepcopy(value)
            except (KeyError, TypeError) as exc:
                task.error_message = f"Cannot bind step input: {exc}"
                transition_task(task, TaskStatus.FAILED)
                db.commit()
                tasks.append(task)
                break
            task.input_json = resolved_input
            db.commit()
            result = self.registry.call(
                step.tool_name,
                resolved_input,
                context=ToolContext(task_id=task.id, actor=self.actor),
                db=db,
            )
            task.output_json = result.output
            task.error_message = result.error_message
            business_status = (result.output or {}).get("status")
            status = TaskStatus.SUCCEEDED
            if result.status != "succeeded" or business_status == "failed":
                status = TaskStatus.FAILED
                task.error_message = result.error_message or str(
                    (result.output or {}).get("errors") or "Tool reported business failure"
                )
            elif business_status == "not_implemented":
                status = TaskStatus.BLOCKED
            elif business_status in {"partial", "skipped"}:
                status = TaskStatus(business_status)
            transition_task(task, status)
            db.add(task)
            db.commit()
            db.refresh(task)
            tasks.append(task)
            if result.output is not None:
                outputs[step.step_id] = result.output

            if status == TaskStatus.BLOCKED or (
                status == TaskStatus.FAILED
                and not (step.continue_on_business_failure and result.status == "succeeded")
            ):
                break

        return tasks
