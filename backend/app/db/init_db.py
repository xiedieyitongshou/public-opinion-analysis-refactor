"""Database initialization helpers."""

from sqlalchemy import Engine, inspect

from app.db.session import Base, engine
from app.models import (
    agent_runtime,  # noqa: F401
    business,  # noqa: F401
)


def init_db(bind: Engine = engine) -> None:
    """Create all registered SQLAlchemy tables.

    Also apply the small SQLite Day 47 observation migration to existing MVP
    databases; full migrations can replace this when schema evolution expands.
    """
    if bind.dialect.name == "sqlite":
        inspector = inspect(bind)
        if "platform_scores" in inspector.get_table_names() and not any(
            column["name"] == "observation_id"
            for column in inspector.get_columns("platform_scores")
        ):
            with bind.begin() as connection:
                connection.exec_driver_sql(
                    "ALTER TABLE platform_scores ADD COLUMN observation_id VARCHAR(255)"
                )
    Base.metadata.create_all(bind=bind)
    if bind.dialect.name == "sqlite":
        with bind.begin() as connection:
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_platform_score_observation "
                "ON platform_scores (event_id, platform, observation_id)"
            )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_event_snapshot_at "
                "ON event_snapshots (event_id, snapshot_at)"
            )
