"""Default Day 11 tool declarations.

The concrete business implementations are filled in later project days. These
placeholders make the registry, schemas, and call logging usable now.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.collectors import default_collector_registry
from app.guardrails import default_guardrail_registry
from app.models import Event, Item, PlatformScore
from app.schemas import (
    CalculateEventScoresInput,
    CalculateEventScoresOutput,
    CreateHumanReviewTaskInput,
    CreateHumanReviewTaskOutput,
    ExtractEventSignalsInput,
    ExtractEventSignalsOutput,
    FetchSourceItemsInput,
    FetchSourceItemsOutput,
    GuardrailCheckInput,
    GuardrailRunResult,
    MatchAndResolveEventsInput,
    MatchAndResolveEventsOutput,
    NormalizeRawItemsInput,
    NormalizeRawItemsOutput,
)
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


class RunBriefingGuardrailsInput(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    subject_type: str | None = "daily_briefing"
    subject_id: str | None = None


class RunBriefingGuardrailsOutput(BaseModel):
    status: str
    blocks_publish: bool
    review_required: bool
    checks: list[dict[str, Any]] = Field(default_factory=list)
    violations: list[dict[str, Any]] = Field(default_factory=list)


def placeholder_handler(input_data: PlaceholderInput, context: ToolContext) -> PlaceholderOutput:
    return PlaceholderOutput(
        message=f"{context.actor} requested a placeholder tool; implementation is scheduled later.",
        data=input_data.payload,
    )


def fetch_source_items_handler(
    input_data: FetchSourceItemsInput,
    context: ToolContext,
) -> FetchSourceItemsOutput:
    return default_collector_registry.collect(input_data, db=context.db_session)


def normalize_raw_items_handler(
    input_data: NormalizeRawItemsInput,
    context: ToolContext,
) -> NormalizeRawItemsOutput:
    from app.agents.normalizer import NormalizerAgent

    return NormalizerAgent().normalize(input_data)


def extract_event_signals_handler(
    input_data: ExtractEventSignalsInput,
    context: ToolContext,
) -> ExtractEventSignalsOutput:
    from app.agents.event_extractor import EventSignalExtractor

    return EventSignalExtractor().extract(input_data)


def match_and_resolve_events_handler(
    input_data: MatchAndResolveEventsInput,
    context: ToolContext,
) -> MatchAndResolveEventsOutput:
    from app.agents.event_resolver import EventResolverAgent

    return EventResolverAgent().resolve(
        input_data,
        db=context.db_session,
        agent_task_id=context.task_id,
        tool_call_id=context.tool_call_id,
    )


def run_briefing_guardrails_handler(
    input_data: RunBriefingGuardrailsInput,
    context: ToolContext,
) -> RunBriefingGuardrailsOutput:
    result: GuardrailRunResult = default_guardrail_registry.run(
        GuardrailCheckInput(
            payload=input_data.payload,
            subject_type=input_data.subject_type,
            subject_id=input_data.subject_id,
            agent_task_id=context.task_id,
            tool_call_id=context.tool_call_id,
        ),
        db=context.db_session,
    )
    return RunBriefingGuardrailsOutput(
        status=result.status,
        blocks_publish=result.blocks_publish,
        review_required=result.review_required,
        checks=[check.model_dump(mode="json") for check in result.checks],
        violations=[violation.model_dump(mode="json") for violation in result.violations],
    )


def create_human_review_task_handler(
    input_data: CreateHumanReviewTaskInput,
    context: ToolContext,
) -> CreateHumanReviewTaskOutput:
    from app.services.human_review import create_human_review_tasks

    if context.db_session is None:
        return CreateHumanReviewTaskOutput(
            status="failed",
            message="create_human_review_task requires a database session.",
        )
    return create_human_review_tasks(
        context.db_session,
        input_data,
        agent_task_id=context.task_id,
    )


def calculate_event_scores_handler(
    input_data: CalculateEventScoresInput,
    context: ToolContext,
) -> CalculateEventScoresOutput:
    from app.services.platform_heat import item_to_platform_heat_signal, score_platform_signals

    db = context.db_session
    if db is None:
        return CalculateEventScoresOutput(
            status="failed",
            errors=["calculate_event_scores requires a database session."],
        )

    event_stmt = select(Event)
    if input_data.event_ids:
        event_stmt = event_stmt.where(Event.event_id.in_(input_data.event_ids))
    events = db.scalars(event_stmt).all()
    if not events:
        return CalculateEventScoresOutput(status="skipped", quality_flags=["no_events"])

    platform_scores = []
    errors: list[str] = []
    saved_count = 0
    for event in events:
        if not event.event_id:
            errors.append(f"Event row {event.id} has no stable event_id.")
            continue
        items = db.scalars(
            select(Item).where(
                Item.event_id == event.id,
                Item.status == "active",
            )
        ).all()
        signals = []
        for item in items:
            signal = item_to_platform_heat_signal(item, event_id=event.event_id)
            if signal.platform in input_data.platforms:
                signals.append(signal)
        for score in score_platform_signals(signals):
            platform_scores.append(score)
            if input_data.dry_run:
                continue
            record = PlatformScore(
                event_id=event.id,
                platform=score.platform,
                raw_score=_primary_raw_score(score.raw_metrics_used),
                normalized_score=score.platform_score,
                score_detail_json={
                    "business_event_id": score.event_id,
                    "platform_bucket": score.platform_bucket,
                    "platform_strength": score.platform_strength,
                    "score_status": score.score_status,
                    "primary_platform_rank": score.primary_platform_rank,
                    "rank_delta": score.rank_delta,
                    "snapshot_presence_count": score.snapshot_presence_count,
                    "raw_metrics_used": score.raw_metrics_used,
                    "sub_scores": score.sub_scores,
                    "platform_presence": score.platform_presence.model_dump(mode="json"),
                    "signal_scores": [
                        signal_score.model_dump(mode="json")
                        for signal_score in score.signal_scores
                    ],
                    "quality_flags": score.quality_flags,
                },
            )
            db.add(record)
            db.commit()
            saved_count += 1

    if platform_scores and not errors:
        status = "succeeded"
    elif platform_scores:
        status = "partial"
    else:
        status = "failed"
    return CalculateEventScoresOutput(
        status=status,
        platform_scores=platform_scores,
        saved_count=saved_count,
        errors=errors,
        quality_flags=["partial_errors"] if errors else [],
    )


def _primary_raw_score(raw_metrics_used: dict[str, Any]) -> float | None:
    for key in (
        "rank",
        "list_position",
        "weibo_search_total_number_proxy",
        "matched_status_count",
    ):
        value = raw_metrics_used.get(key)
        if isinstance(value, int | float):
            return float(value)
    return None


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

    registry.register(
        ToolDefinition(
            name="fetch_source_items",
            description="Dispatch registered collectors and return crawl validation results.",
            input_model=FetchSourceItemsInput,
            output_model=FetchSourceItemsOutput,
            handler=fetch_source_items_handler,
            has_side_effect=True,
            retryable=True,
            max_retries=1,
        )
    )

    registry.register(
        ToolDefinition(
            name="normalize_raw_items",
            description="Deterministically normalize source-mapped items into NormalizedItem.",
            input_model=NormalizeRawItemsInput,
            output_model=NormalizeRawItemsOutput,
            handler=normalize_raw_items_handler,
            has_side_effect=False,
            retryable=False,
            max_retries=0,
        )
    )

    placeholder_tools = [
        "generate_daily_briefing",
        "review_briefing_quality",
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
            name="calculate_event_scores",
            description=(
                "Calculate Day 44 platform-local Zhihu/Weibo heat scores and persist "
                "PlatformScore rows."
            ),
            input_model=CalculateEventScoresInput,
            output_model=CalculateEventScoresOutput,
            handler=calculate_event_scores_handler,
            has_side_effect=True,
            retryable=False,
            max_retries=0,
        )
    )

    registry.register(
        ToolDefinition(
            name="extract_event_signals",
            description=(
                "Extract strict EventSignal records from SourceSignal input using "
                "the Day 38 rule-first pipeline."
            ),
            input_model=ExtractEventSignalsInput,
            output_model=ExtractEventSignalsOutput,
            handler=extract_event_signals_handler,
            has_side_effect=False,
            retryable=True,
            max_retries=1,
        )
    )

    registry.register(
        ToolDefinition(
            name="match_and_resolve_events",
            description="Resolve EventSignal records into stable event IDs with Day 39 matching.",
            input_model=MatchAndResolveEventsInput,
            output_model=MatchAndResolveEventsOutput,
            handler=match_and_resolve_events_handler,
            has_side_effect=False,
            retryable=True,
            max_retries=1,
        )
    )
    registry.register(
        ToolDefinition(
            name="run_briefing_guardrails",
            description="Run MVP guardrails over briefing or event-card payloads.",
            input_model=RunBriefingGuardrailsInput,
            output_model=RunBriefingGuardrailsOutput,
            handler=run_briefing_guardrails_handler,
            has_side_effect=False,
            retryable=False,
            max_retries=0,
        )
    )

    registry.register(
        ToolDefinition(
            name="create_human_review_task",
            description=(
                "Create or update Day 41 human review tasks from candidate_review payloads."
            ),
            input_model=CreateHumanReviewTaskInput,
            output_model=CreateHumanReviewTaskOutput,
            handler=create_human_review_task_handler,
            has_side_effect=True,
            retryable=False,
            max_retries=0,
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
