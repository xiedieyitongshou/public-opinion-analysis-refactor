"""Database initialization helpers."""

from sqlalchemy import Engine

from app.db.session import Base, engine
from app.models import (
    agent_runtime,  # noqa: F401
    business,  # noqa: F401
)


def init_db(bind: Engine = engine) -> None:
    """Create all registered SQLAlchemy tables.

    This is sufficient for the SQLite MVP. Alembic migrations can replace this
    once schema evolution becomes a real requirement.
    """
    Base.metadata.create_all(bind=bind)
