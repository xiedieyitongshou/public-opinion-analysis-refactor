"""Default Day 11 tool declarations.

The concrete business implementations are filled in later project days. These
placeholders make the registry, schemas, and call logging usable now.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.tools.runtime import ToolContext, ToolDefinition, ToolRegistry


class PlaceholderInput(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class PlaceholderOutput(BaseModel):
    status: Literal["not_implemented"] = "not_implemented"
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class SearchExistingEvidenceInput(BaseModel):
    query_text: str = Field(min_length=1)
    event_id: int | None = None
    limit: int = Field(default=20, ge=1, le=100)
    filters: dict[str, Any] = Field(default_factory=dict)


class SearchExistingEvidenceOutput(BaseModel):
    status: Literal["not_implemented"] = "not_implemented"
    query_text: str
    related_events: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_evidence: list[dict[str, Any]] = Field(default_factory=list)
    source_citations: list[dict[str, Any]] = Field(default_factory=list)
    message: str


def placeholder_handler(input_data: PlaceholderInput, context: ToolContext) -> PlaceholderOutput:
    return PlaceholderOutput(
        message=f"{context.actor} requested a placeholder tool; implementation is scheduled later.",
        data=input_data.payload,
    )


def search_existing_evidence_handler(
    input_data: SearchExistingEvidenceInput,
    context: ToolContext,
) -> SearchExistingEvidenceOutput:
    return SearchExistingEvidenceOutput(
        query_text=input_data.query_text,
        message=(
            "search_existing_evidence is registered for Mode B, but retrieval "
            "implementation is scheduled for a later milestone."
        ),
    )


def build_default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()

    placeholder_tools = [
        "fetch_source_items",
        "normalize_raw_items",
        "extract_event_signals",
        "match_and_resolve_events",
        "calculate_event_scores",
        "generate_daily_briefing",
        "review_briefing_quality",
        "run_briefing_guardrails",
        "create_human_review_task",
        "save_daily_report",
        "render_briefing_image",
        "run_evaluation_suite",
    ]

    for name in placeholder_tools:
        registry.register(
            ToolDefinition(
                name=name,
                description=f"Placeholder declaration for {name}.",
                input_model=PlaceholderInput,
                output_model=PlaceholderOutput,
                handler=placeholder_handler,
                has_side_effect=name
                in {
                    "create_human_review_task",
                    "save_daily_report",
                    "render_briefing_image",
                },
                retryable=True,
                max_retries=1,
            )
        )

    registry.register(
        ToolDefinition(
            name="search_existing_evidence",
            description="Mode B placeholder for retrieving existing event evidence.",
            input_model=SearchExistingEvidenceInput,
            output_model=SearchExistingEvidenceOutput,
            handler=search_existing_evidence_handler,
            has_side_effect=False,
            retryable=False,
            max_retries=0,
        )
    )
    return registry


default_tool_registry = build_default_tool_registry()
