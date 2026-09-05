from sqlalchemy import create_engine, inspect

from app.db.init_db import init_db


def test_day9_agent_runtime_tables_are_created() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    init_db(engine)

    table_names = set(inspect(engine).get_table_names())
    assert {
        "agent_tasks",
        "agent_tool_calls",
        "human_review_tasks",
        "guardrail_violations",
        "evaluation_runs",
        "evaluation_cases",
    }.issubset(table_names)
