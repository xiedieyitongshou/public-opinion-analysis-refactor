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
    input_bindings: dict[str, str] = Field(default_factory=dict)
    continue_on_business_failure: bool = False


class Plan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    mode: Literal[
        "daily_briefing",
        "event_search",
        "daily_hotspot_collection",
        "crawl_validation",
        "hotspot_analysis",
    ]
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
            for binding in step.input_bindings.values():
                dependency = binding.split(".", 1)[0]
                if dependency not in step.depends_on:
                    raise ValueError(f"Binding {binding} must reference a declared dependency")
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
    daily_collection_source_ids: tuple[str, ...] = (
        "zhihu_hot_list",
        "weibo_rsshub_hot_search",
        "chinanews_scroll_rss",
        "people_politics_rss",
        "xinhua_politics_rss",
    )
    validation_source_ids: tuple[str, ...] = (
        "zhihu_hot_list",
        "zhihu_search",
        "weibo_rsshub_hot_search",
        "chinanews_scroll_rss",
        "people_politics_rss",
        "xinhua_politics_rss",
    )
    postponed_source_ids: tuple[str, ...] = (
        "weibo_direct_hot_search",
        "weibo_cli_comments",
        "weibo_cli_reposts",
        "bilibili_hot_video",
        "douyin_hot",
        "xiaohongshu_hot",
        "wechat_article_fulltext",
    )
    validation_limits: dict[str, int] = {
        "zhihu_hot_list": 10,
        "zhihu_search": 3,
        "weibo_rsshub_hot_search": 10,
        "chinanews_scroll_rss": 10,
        "people_politics_rss": 10,
        "xinhua_politics_rss": 10,
    }

    def create_hotspot_analysis_plan(self, payload: dict[str, Any] | None = None) -> Plan:
        from app.agents.analysis import build_hotspot_analysis_plan
        from app.schemas.analysis import HotspotAnalysisInput

        return build_hotspot_analysis_plan(HotspotAnalysisInput.model_validate(payload or {}))

    def create_daily_briefing_plan(self, payload: dict[str, Any] | None = None) -> Plan:
        payload = payload or {}
        if not payload.get("run_id"):
            raise ValueError("Daily briefing requires an existing analysis run_id")
        return Plan(
            name="Snapshot, review and save daily briefing", mode="daily_briefing",
            steps=[
                PlanStep(step_id="generate", name="Generate briefing",
                         tool_name="generate_daily_briefing",
                         input_json={"run_id": payload["run_id"]}),
                PlanStep(step_id="review", name="Review briefing quality",
                         tool_name="review_briefing_quality", depends_on=["generate"],
                         input_bindings={"briefing": "generate.briefing"}),
                PlanStep(step_id="save", name="Save immutable report version",
                         tool_name="save_daily_report", depends_on=["review"],
                         input_bindings={"briefing": "review.briefing"}),
            ],
        )

    def create_daily_hotspot_collection_plan(
        self,
        payload: dict[str, Any] | None = None,
    ) -> Plan:
        payload = payload or {}
        source_ids = _without_postponed(
            payload.get("source_ids") or list(self.daily_collection_source_ids),
            self.postponed_source_ids,
        )
        fetch_input = {
            "source_ids": source_ids,
            "run_id": payload.get("run_id"),
            "limit": int(payload.get("limit", 20)),
            "per_source_limits": payload.get("per_source_limits", {}),
            "validate_only": False,
            "dry_run": False,
            "promote_to_event_pool": bool(payload.get("promote_to_event_pool", True)),
            "include_items": bool(payload.get("include_items", True)),
            "timeout_seconds": payload.get("timeout_seconds"),
            "max_quota_cost": payload.get("max_quota_cost"),
            "per_source_params": payload.get("per_source_params", {}),
            "postponed_source_ids": list(self.postponed_source_ids),
        }
        return Plan(
            name="Daily hotspot collection workflow",
            mode="daily_hotspot_collection",
            steps=[
                PlanStep(
                    step_id="fetch_daily_hotspot_sources",
                    name="Fetch daily hotspot sources",
                    tool_name="fetch_source_items",
                    input_json=_drop_none(fetch_input),
                )
            ],
            metadata={
                "promote_to_event_pool": fetch_input["promote_to_event_pool"],
                "source_ids": source_ids,
            },
        )

    def create_crawl_validation_plan(
        self,
        payload: dict[str, Any] | None = None,
    ) -> Plan:
        payload = payload or {}
        source_ids = _without_postponed(
            payload.get("source_ids") or list(self.validation_source_ids),
            self.postponed_source_ids,
        )
        per_source_params = dict(payload.get("per_source_params", {}))
        zhihu_queries = payload.get("zhihu_search_queries")
        zhihu_candidates = payload.get("zhihu_search_candidates")
        if zhihu_queries or zhihu_candidates:
            per_source_params["zhihu_search"] = {
                **per_source_params.get("zhihu_search", {}),
                "queries": zhihu_queries or [],
                "candidates": zhihu_candidates or [],
                "max_queries": int(payload.get("zhihu_search_max_queries", 3)),
            }
        per_source_params.setdefault(
            "weibo_rsshub_hot_search",
            {"with_cli": bool(payload.get("with_weibo_cli", False)), "cli_topic_limit": 1},
        )

        fetch_input = {
            "source_ids": source_ids,
            "run_id": payload.get("run_id"),
            "limit": int(payload.get("limit", 10)),
            "per_source_limits": {
                **self.validation_limits,
                **payload.get("per_source_limits", {}),
            },
            "validate_only": True,
            "dry_run": True,
            "promote_to_event_pool": False,
            "include_items": bool(payload.get("include_items", True)),
            "timeout_seconds": payload.get("timeout_seconds"),
            "max_quota_cost": payload.get("max_quota_cost"),
            "per_source_params": per_source_params,
            "postponed_source_ids": list(self.postponed_source_ids),
        }
        return Plan(
            name="Crawl validation workflow",
            mode="crawl_validation",
            steps=[
                PlanStep(
                    step_id="validate_collector_sources",
                    name="Validate collector sources",
                    tool_name="fetch_source_items",
                    input_json=_drop_none(fetch_input),
                )
            ],
            metadata={
                "promote_to_event_pool": False,
                "source_ids": source_ids,
                "validation_only": True,
            },
        )

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


def _without_postponed(source_ids: list[str], postponed_source_ids: tuple[str, ...]) -> list[str]:
    postponed = set(postponed_source_ids)
    return [source_id for source_id in source_ids if source_id not in postponed]


def _drop_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}
