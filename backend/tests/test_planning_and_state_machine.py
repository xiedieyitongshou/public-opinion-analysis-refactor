from datetime import date

import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.agents import (
    AgentTaskRunner,
    DraftStatus,
    Planner,
    StateTransitionError,
    TaskStatus,
    can_transition_draft,
    can_transition_task,
    transition_draft,
    transition_task,
    validate_structured_output,
)
from app.db.init_db import init_db
from app.models import AgentTask, AgentToolCall, DailyReport
from app.tools import default_tool_registry


class ExampleOutput(BaseModel):
    title: str
    score: float


def make_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    init_db(engine)
    return sessionmaker(bind=engine)()


def test_planner_creates_linear_daily_briefing_plan() -> None:
    plan = Planner().create_daily_briefing_plan({"source_limit": 10})

    assert plan.mode == "daily_briefing"
    assert [step.tool_name for step in plan.steps] == [
        "fetch_source_items",
        "normalize_raw_items",
        "extract_event_signals",
        "match_and_resolve_events",
        "calculate_event_scores",
        "generate_daily_briefing",
        "review_briefing_quality",
        "run_briefing_guardrails",
        "create_human_review_task",
    ]
    assert plan.steps[1].depends_on == ["fetch_source_items"]


def test_planner_creates_event_search_placeholder_plan() -> None:
    plan = Planner().create_event_search_plan("sample event")

    assert plan.mode == "event_search"
    assert len(plan.steps) == 1
    assert plan.steps[0].tool_name == "search_existing_evidence"
    assert plan.steps[0].input_json == {"query_text": "sample event"}


def test_task_state_machine_allows_only_valid_transitions() -> None:
    task = AgentTask(task_type="fetch_source_items", status=TaskStatus.PENDING.value)

    assert can_transition_task("pending", "running")
    transition_task(task, TaskStatus.RUNNING)
    assert task.status == "running"

    with pytest.raises(StateTransitionError):
        transition_task(task, TaskStatus.RETRYING)


def test_draft_state_machine_blocks_direct_publish_from_draft() -> None:
    report = DailyReport(
        report_date=date(2026, 9, 6),
        status=DraftStatus.DRAFT.value,
        title="Daily briefing",
        briefing_json={"sections": []},
    )

    assert not can_transition_draft("draft", "published")
    with pytest.raises(StateTransitionError):
        transition_draft(report, DraftStatus.PUBLISHED)

    transition_draft(report, DraftStatus.DRAFT_READY_FOR_REVIEW)
    transition_draft(report, DraftStatus.PUBLISHED)
    assert report.status == "published"


def test_structured_output_validation_reports_errors() -> None:
    valid = validate_structured_output(ExampleOutput, {"title": "event", "score": 0.8})
    invalid = validate_structured_output(ExampleOutput, {"title": "event"})

    assert valid.is_valid
    assert valid.output == {"title": "event", "score": 0.8}
    assert not invalid.is_valid
    assert invalid.error_message


def test_runner_executes_linear_plan_and_logs_tool_calls() -> None:
    session = make_session()
    plan = Planner().create_event_search_plan("sample event")
    runner = AgentTaskRunner(default_tool_registry)

    tasks = runner.run_plan(plan, session)

    persisted_tasks = session.scalars(select(AgentTask)).all()
    tool_calls = session.scalars(select(AgentToolCall)).all()
    assert len(tasks) == 1
    assert persisted_tasks[0].status == "succeeded"
    assert persisted_tasks[0].plan_id == plan.plan_id
    assert persisted_tasks[0].output_json is not None
    assert len(tool_calls) == 1
    assert tool_calls[0].tool_name == "search_existing_evidence"
    assert tool_calls[0].status == "succeeded"
