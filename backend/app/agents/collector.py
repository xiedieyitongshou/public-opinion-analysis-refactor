"""Collector Agent orchestration for Day 34."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.planning import Plan, Planner
from app.agents.runner import AgentTaskRunner
from app.models import AgentTask, CrawlValidationRun
from app.tools import ToolRegistry, default_tool_registry


class CollectorAgentRunResult(BaseModel):
    """Compact return object for collection orchestration runs."""

    plan_id: str
    mode: str
    status: str
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    fetch_output: dict[str, Any] | None = None
    crawl_validation_run_id: int | None = None


class CollectorAgent:
    """Plan-driven agent that dispatches source collectors through Tool Calling."""

    def __init__(
        self,
        *,
        planner: Planner | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.planner = planner or Planner()
        self.runner = AgentTaskRunner(registry or default_tool_registry, actor="collector_agent")

    def run_daily_hotspot_collection(
        self,
        db: Session,
        payload: dict[str, Any] | None = None,
    ) -> CollectorAgentRunResult:
        plan = self.planner.create_daily_hotspot_collection_plan(payload)
        tasks = self.runner.run_plan(plan, db)
        return self._result_from_tasks(plan, tasks)

    def run_crawl_validation(
        self,
        db: Session,
        payload: dict[str, Any] | None = None,
    ) -> CollectorAgentRunResult:
        plan = self.planner.create_crawl_validation_plan(payload)
        tasks = self.runner.run_plan(plan, db)
        fetch_output = _last_task_output(tasks)
        crawl_validation_run = self._record_crawl_validation_run(db, plan, tasks, fetch_output)
        result = self._result_from_tasks(plan, tasks, fetch_output=fetch_output)
        return result.model_copy(update={"crawl_validation_run_id": crawl_validation_run.id})

    def run_plan(self, db: Session, plan: Plan) -> CollectorAgentRunResult:
        tasks = self.runner.run_plan(plan, db)
        fetch_output = _last_task_output(tasks)
        if plan.mode == "crawl_validation":
            crawl_validation_run = self._record_crawl_validation_run(db, plan, tasks, fetch_output)
            return self._result_from_tasks(
                plan,
                tasks,
                fetch_output=fetch_output,
            ).model_copy(update={"crawl_validation_run_id": crawl_validation_run.id})
        return self._result_from_tasks(plan, tasks, fetch_output=fetch_output)

    def _result_from_tasks(
        self,
        plan: Plan,
        tasks: list[AgentTask],
        *,
        fetch_output: dict[str, Any] | None = None,
    ) -> CollectorAgentRunResult:
        if fetch_output is None:
            fetch_output = _last_task_output(tasks)
        return CollectorAgentRunResult(
            plan_id=plan.plan_id,
            mode=plan.mode,
            status=_run_status(tasks, fetch_output),
            tasks=[_task_summary(task) for task in tasks],
            fetch_output=fetch_output,
        )

    def _record_crawl_validation_run(
        self,
        db: Session,
        plan: Plan,
        tasks: list[AgentTask],
        fetch_output: dict[str, Any] | None,
    ) -> CrawlValidationRun:
        fetch_output = fetch_output or {}
        source_results = fetch_output.get("results") or []
        run = CrawlValidationRun(
            plan_id=plan.plan_id,
            status=str(fetch_output.get("status") or _run_status(tasks, fetch_output)),
            requested_source_ids_json=fetch_output.get("requested_source_ids") or [],
            skipped_source_ids_json=fetch_output.get("skipped_source_ids") or [],
            unavailable_source_ids_json=fetch_output.get("unavailable_source_ids") or [],
            promote_to_event_pool=bool(fetch_output.get("promote_to_event_pool", False)),
            source_results_json=source_results,
            summary_json={
                "validation_summary": fetch_output.get("validation_summary") or {},
                "field_completeness_summary": fetch_output.get("field_completeness_summary") or {},
                "source_status_summary": fetch_output.get("source_status_summary") or {},
                "contribution_roles_by_source": fetch_output.get(
                    "contribution_roles_by_source"
                )
                or {},
            },
            raw_sample_manifest_json=_raw_sample_manifest(source_results),
            started_at=tasks[0].started_at if tasks else datetime.now(UTC),
            finished_at=tasks[-1].finished_at if tasks else datetime.now(UTC),
            error_message=_first_error(tasks, fetch_output),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run


def _last_task_output(tasks: list[AgentTask]) -> dict[str, Any] | None:
    if not tasks:
        return None
    return tasks[-1].output_json


def _run_status(tasks: list[AgentTask], fetch_output: dict[str, Any] | None) -> str:
    if fetch_output and fetch_output.get("status"):
        return str(fetch_output["status"])
    if not tasks:
        return "skipped"
    if any(task.status == "failed" for task in tasks):
        return "failed"
    if all(task.status == "succeeded" for task in tasks):
        return "succeeded"
    return "partial"


def _task_summary(task: AgentTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "title": task.title,
        "plan_id": task.plan_id,
        "retry_count": task.retry_count,
        "error_message": task.error_message,
    }


def _first_error(tasks: list[AgentTask], fetch_output: dict[str, Any] | None) -> str | None:
    for task in tasks:
        if task.error_message:
            return task.error_message
    if fetch_output:
        for result in fetch_output.get("results") or []:
            if result.get("error_message") and result.get("status") == "failed":
                return result["error_message"]
    return None


def _raw_sample_manifest(source_results: list[dict[str, Any]]) -> dict[str, Any]:
    paths = {
        result["source_id"]: result.get("raw_sample_path")
        for result in source_results
        if result.get("raw_sample_path")
    }
    return {"raw_sample_paths": paths, "raw_sample_count": len(paths)}
