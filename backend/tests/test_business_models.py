from sqlalchemy import create_engine, inspect

from app.db.init_db import init_db


def test_day10_business_tables_are_created() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    init_db(engine)

    table_names = set(inspect(engine).get_table_names())
    assert {
        "sources",
        "items",
        "events",
        "platform_scores",
        "event_snapshots",
        "daily_reports",
        "event_queries",
    }.issubset(table_names)
