import sqlite3
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.init_db import init_db
from app.models import Event
from app.models.briefing import AnalysisRun, EmailConfiguration
from app.schemas.collection_session import CollectionSessionConfig
from app.services import overnight_test
from app.services.collection_backup import backup_and_clear


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "test.db"))
    init_db(engine)
    monkeypatch.setattr(overnight_test, "preflight", lambda db: None)
    with Session(engine) as session:
        session.add(Event(event_id="old", title="old event"))
        session.add(EmailConfiguration(
            id=1, values_json={"email_schedule_enabled": False, "daily_briefing_enabled": False},
            updated_at=datetime.now(UTC),
        ))
        session.commit()
        yield session
    engine.dispose()


def test_backup_contains_old_data_and_new_session_is_clean(db):
    config = CollectionSessionConfig(fresh_start=True, report_scope="last24h")
    row = overnight_test.start(db, config=config)
    assert row.status == "running"
    assert row.config_json["report_scope"] == "session"
    assert db.scalar(select(Event)) is None
    assert db.get(EmailConfiguration, 1) is not None
    from pathlib import Path

    path = Path(db.get_bind().url.database).parent / "backups" / row.config_json["backup_file"]
    with sqlite3.connect(path) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT event_id FROM events").fetchone()[0] == "old"
    with pytest.raises(ValueError, match="已有采集"):
        overnight_test.start(db, config=config)


def test_failed_backup_keeps_old_data(db, monkeypatch):
    monkeypatch.setattr("app.services.collection_backup.sqlite3.connect",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(OSError):
        backup_and_clear(db)
    assert db.scalar(select(Event.event_id)) == "old"


def test_reset_rolls_back_if_new_session_fails(db):
    backup_and_clear(db)
    assert db.scalar(select(Event)) is None
    db.rollback()
    assert db.scalar(select(Event.event_id)) == "old"


def test_active_analysis_blocks_reset(db):
    db.add(AnalysisRun(run_id="busy", status="queued", started_at=datetime.now(UTC),
                       input_json={}))
    db.commit()
    with pytest.raises(ValueError, match="等待"):
        backup_and_clear(db)
    assert db.scalar(select(Event.event_id)) == "old"
