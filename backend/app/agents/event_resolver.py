"""Event Resolver Agent MVP for Day 40."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.agents.event_matcher import EventMatcher
from app.guardrails import default_guardrail_registry
from app.guardrails.event_merge import apply_merge_guardrails
from app.schemas import MatchAndResolveEventsInput, MatchAndResolveEventsOutput


class EventResolverAgent:
    """Coordinate matching, merge guardrails, and final EventResolution output."""

    def __init__(self, matcher: EventMatcher | None = None) -> None:
        self.matcher = matcher or EventMatcher()

    def resolve(
        self,
        input_data: MatchAndResolveEventsInput,
        *,
        db: Session | None = None,
        agent_task_id: int | None = None,
        tool_call_id: int | None = None,
    ) -> MatchAndResolveEventsOutput:
        matched = self.matcher.resolve(input_data)
        final_resolutions = [
            apply_merge_guardrails(
                resolution,
                event_signal=signal.model_dump(mode="json"),
                match_config=input_data.match_config,
                registry=default_guardrail_registry,
                db=db,
                agent_task_id=agent_task_id,
                tool_call_id=tool_call_id,
            )
            for signal, resolution in zip(
                input_data.event_signals,
                matched.event_resolutions,
                strict=False,
            )
        ]
        return matched.model_copy(
            update={
                "event_resolutions": final_resolutions,
                "created_event_count": sum(
                    1 for item in final_resolutions if item.action == "create"
                ),
                "merged_event_count": sum(
                    1 for item in final_resolutions if item.action == "merge"
                ),
                "review_required_count": sum(
                    1 for item in final_resolutions if item.action == "candidate_review"
                ),
                "rejected_event_count": sum(
                    1 for item in final_resolutions if item.action == "reject"
                ),
            }
        )
