"""Tool-driven linear workflow for the three implemented business stages."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.planning import Plan, Planner, PlanStep
from app.agents.runner import AgentTaskRunner
from app.core.config import settings
from app.models import AgentTask
from app.schemas.analysis import HotspotAnalysisInput, HotspotAnalysisOutput
from app.schemas.collectors import FetchSourceItemsOutput
from app.services.matching_profiles import event_config
from app.tools import ToolRegistry, default_tool_registry


def build_hotspot_analysis_plan(data: HotspotAnalysisInput) -> Plan:
    collection = data.collection.model_dump(mode="json")
    collection.update(
        run_id=data.run_id,
        validate_only=False,
        dry_run=False,
        promote_to_event_pool=False,
        include_items=True,
        source_ids=data.collection.source_ids or list(Planner.daily_collection_source_ids),
        postponed_source_ids=list(
            dict.fromkeys([*Planner.postponed_source_ids, *data.collection.postponed_source_ids])
        ),
    )
    steps = []

    def step(step_id, tool, inputs=None, bindings=None, **kwargs):
        bindings = bindings or {}
        dependencies = list(
            dict.fromkeys(
                ([steps[-1].step_id] if steps else [])
                + [value.split(".", 1)[0] for value in bindings.values()]
            )
        )
        steps.append(
            PlanStep(
                step_id=step_id,
                name=tool,
                tool_name=tool,
                input_json=inputs or {},
                input_bindings=bindings,
                depends_on=dependencies,
                **kwargs,
            )
        )

    step("collect", "fetch_source_items", collection, continue_on_business_failure=True)
    step("normalize", "normalize_raw_items", bindings={"items": "collect.items"})
    step(
        "prepare",
        "prepare_source_signals",
        {
            "run_id": data.run_id,
            "window_end": data.window_end.isoformat() if data.window_end else None,
        },
        {"items": "normalize.items", "collection": "collect"},
    )
    step(
        "extract",
        "extract_event_signals",
        {
            "run_id": data.run_id,
            "use_llm": data.use_llm,
        },
        {"source_signals": "prepare.source_signals"},
    )
    step(
        "resolve",
        "match_and_resolve_events",
        {
            "run_id": data.run_id,
            "persist": True,
            "discovery_mode": "automatic",
            "match_config": event_config(
                data.matching_profile or settings.matching_profile
            ).model_dump(mode="json"),
        },
        {
            "event_signals": "extract.event_signals",
            "source_signals": "prepare.source_signals",
            "normalized_items": "prepare.items",
            "source_refs_by_signal_id": "prepare.source_refs_by_signal_id",
            "window_end": "prepare.window_end",
        },
    )
    step(
        "review",
        "create_human_review_task",
        {"run_id": data.run_id},
        {
            "payload": "resolve.review_payload",
        },
    )
    step(
        "score",
        "calculate_event_scores",
        {"scoped": True},
        {
            "event_ids": "resolve.event_ids",
            "collection": "collect",
            "event_ids_by_content_hash": "resolve.event_ids_by_content_hash",
        },
    )
    step(
        "classify",
        "classify_events",
        {
            "run_id": data.run_id,
            "matching_profile": data.matching_profile or settings.matching_profile,
            "official_search_enabled": (
                settings.official_search_enabled
                if data.official_search_enabled is None
                else data.official_search_enabled
            ),
        },
        {
            "event_ids": "resolve.event_ids",
            "collection": "collect",
            "event_resolutions": "resolve.event_resolutions",
            "window_end": "prepare.window_end",
        },
    )
    step(
        "analyze",
        "analyze_event_heat",
        {
            "run_id": data.run_id,
            "scoped": True,
            "configs": {
                key: value.model_dump(mode="json") for key, value in data.trend_configs.items()
            },
        },
        {"event_ids": "resolve.event_ids", "window_end": "prepare.window_end"},
    )
    return Plan(
        name="Hotspot collection, event construction and analysis",
        mode="hotspot_analysis",
        steps=steps,
        metadata={"run_id": data.run_id},
    )


class HotspotAnalysisAgent:
    def __init__(self, registry: ToolRegistry | None = None):
        self.registry = registry or default_tool_registry

    def run(self, db: Session, data: HotspotAnalysisInput) -> HotspotAnalysisOutput:
        plan = build_hotspot_analysis_plan(data)
        registry = self._registry_for_run(db, plan)
        tasks = AgentTaskRunner(registry, actor="hotspot_analysis_agent").run_plan(plan, db)
        outputs = {task.task_type: task.output_json or {} for task in tasks}
        prepared = outputs.get("prepare_source_signals", {})
        resolved = outputs.get("match_and_resolve_events", {})
        classified = outputs.get("classify_events", {})
        analyzed = outputs.get("analyze_event_heat", {})
        errors = [task.error_message for task in tasks if task.error_message]
        source_results = outputs.get("fetch_source_items", {}).get("results", [])
        errors.extend(
            result["error_message"] for result in source_results if result.get("error_message")
        )
        for output in outputs.values():
            errors.extend(output.get("errors") or [])
        if len(tasks) != len(plan.steps) or tasks[-1].status in {"failed", "blocked"}:
            status = "failed"
        elif any(task.status in {"failed", "partial"} for task in tasks):
            status = "partial" if resolved.get("event_ids") else "failed"
            if resolved.get("review_required_count"):
                status = "partial"
        elif not resolved.get("event_ids"):
            status = "skipped"
        elif any(result["status"] != "ok" for result in analyzed.get("analyses", [])):
            status = "partial"
        else:
            status = "succeeded"
        return HotspotAnalysisOutput(
            run_id=data.run_id,
            plan_id=plan.plan_id,
            status=status,
            window_end=prepared.get("window_end"),
            event_ids=resolved.get("event_ids", []),
            source_statuses={result["source_id"]: result["status"] for result in source_results},
            classifications=classified.get("classifications", []),
            analyses=analyzed.get("analyses", []),
            errors=list(dict.fromkeys(errors)),
            quality_flags=sorted(
                {flag for output in outputs.values() for flag in output.get("quality_flags", [])}
            ),
            tasks=[
                {
                    "id": task.id,
                    "tool": task.task_type,
                    "status": task.status,
                    "error": task.error_message,
                }
                for task in tasks
            ],
        )

    def _registry_for_run(self, db: Session, plan: Plan) -> ToolRegistry:
        # run_id identifies a collection round. Retrying it reuses the recorded
        # source response instead of turning a retry into another observation.
        fetch_input = plan.steps[0].input_json
        previous = db.scalars(
            select(AgentTask)
            .where(
                AgentTask.task_type == "fetch_source_items",
                AgentTask.input_json["run_id"].as_string() == fetch_input["run_id"],
            )
            .order_by(AgentTask.id)
        ).all()
        for task in previous:
            if task.output_json is None:
                continue
            if task.input_json != fetch_input:
                raise ValueError("The same run_id cannot use different collection parameters")
            prepared = db.scalar(
                select(AgentTask).where(
                    AgentTask.plan_id == task.plan_id,
                    AgentTask.task_type == "prepare_source_signals",
                    AgentTask.status.in_(["succeeded", "partial", "skipped"]),
                )
            )
            if prepared is not None:
                original_end = prepared.output_json["window_end"]
                supplied_end = plan.steps[2].input_json.get("window_end")
                if supplied_end is not None and datetime.fromisoformat(
                    supplied_end
                ) != datetime.fromisoformat(original_end):
                    raise ValueError("The same run_id must reuse its original window_end")
                plan.steps[2].input_json["window_end"] = original_end
            cached = FetchSourceItemsOutput.model_validate(task.output_json)
            registry = ToolRegistry()
            for tool in self.registry.list_tools():
                if tool.name == "fetch_source_items":
                    tool = replace(
                        tool,
                        handler=lambda data, context, value=cached: value.model_copy(deep=True),
                    )
                registry.register(tool)
            return registry
        return self.registry
