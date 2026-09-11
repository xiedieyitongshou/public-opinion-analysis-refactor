from sqlalchemy import create_engine, inspect

from app.db.init_db import init_db


def test_day10_business_tables_are_created() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    init_db(engine)
    inspector = inspect(engine)

    table_names = set(inspector.get_table_names())
    assert {
        "sources",
        "items",
        "events",
        "platform_scores",
        "event_snapshots",
        "daily_reports",
        "event_queries",
    }.issubset(table_names)

    source_columns = {column["name"] for column in inspector.get_columns("sources")}
    assert {"source_status", "source_origin"}.issubset(source_columns)

    item_columns = {column["name"] for column in inspector.get_columns("items")}
    assert {
        "source_status",
        "source_origin",
        "signal_role",
        "quality_flags_json",
        "signal_contribution_roles_json",
    }.issubset(item_columns)
