"""Minimal linear agent task runner."""

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

            result = self.registry.call(
                step.tool_name,
                step.input_json,
                context=ToolContext(task_id=task.id, actor=self.actor),
                db=db,
            )
            task.output_json = result.output
            task.error_message = result.error_message
            transition_task(
                task,
                TaskStatus.SUCCEEDED if result.status == "succeeded" else TaskStatus.FAILED,
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            tasks.append(task)

            if task.status != TaskStatus.SUCCEEDED.value:
                break

        return tasks
