from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.init_db import init_db
from app.models import AgentToolCall
from app.tools import ToolContext, ToolDefinition, ToolRegistry, default_tool_registry


class EchoInput(BaseModel):
    text: str


class EchoOutput(BaseModel):
    text: str


def test_default_registry_declares_day11_tools() -> None:
    names = {tool.name for tool in default_tool_registry.list_tools()}

    assert {
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
        "search_existing_evidence",
    }.issubset(names)


def test_tool_call_validates_schema_and_writes_success_log() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    init_db(engine)
    session = sessionmaker(bind=engine)()
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="echo",
            description="Echo input text.",
            input_model=EchoInput,
            output_model=EchoOutput,
            handler=lambda input_data, context: EchoOutput(text=input_data.text),
        )
    )

    result = registry.call("echo", {"text": "hello"}, context=ToolContext(task_id=None), db=session)

    logs = session.scalars(select(AgentToolCall)).all()
    assert result.status == "succeeded"
    assert result.output == {"text": "hello"}
    assert len(logs) == 1
    assert logs[0].tool_name == "echo"
    assert logs[0].status == "succeeded"
    assert logs[0].output_json == {"text": "hello"}


def test_tool_call_writes_failed_log_for_invalid_input() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    init_db(engine)
    session = sessionmaker(bind=engine)()
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="echo",
            description="Echo input text.",
            input_model=EchoInput,
            output_model=EchoOutput,
            handler=lambda input_data, context: EchoOutput(text=input_data.text),
        )
    )

    result = registry.call("echo", {"wrong": "shape"}, db=session)

    logs = session.scalars(select(AgentToolCall)).all()
    assert result.status == "failed"
    assert "Invalid input for echo" in str(result.error_message)
    assert len(logs) == 1
    assert logs[0].status == "failed"
    assert "Invalid input for echo" in str(logs[0].error_message)


def test_search_existing_evidence_placeholder_is_callable() -> None:
    result = default_tool_registry.call(
        "search_existing_evidence",
        {"query_text": "example event"},
    )

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["status"] == "not_implemented"
    assert result.output["query_text"] == "example event"
