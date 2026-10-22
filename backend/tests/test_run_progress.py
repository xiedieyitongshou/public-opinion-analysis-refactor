from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.init_db import init_db
from app.models.briefing import AnalysisRun, RequestLog
from app.services.run_progress import current_progress, emit_progress, progress_path, progress_scope


def test_live_progress_persists_and_terminal_state_overrides_running(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'progress-test.db'))
    init_db(engine)
    with Session(engine) as db:
        run = AnalysisRun(run_id='run-1', status='running', started_at=datetime.now(UTC),
                          input_json={'collection': {
                              'source_ids': ['zhihu_hot_list', 'weibo_cli']}})
        db.add(run)
        db.commit()
        with progress_scope(engine, run.run_id):
            emit_progress(stage='fetch_source_items', source='weibo_cli', status='running',
                          completed=8, total=20, returned_count=80, accepted_count=42)
        db.add(RequestLog(run_id='run-1', source_id='weibo_cli', operation='search',
                          unit='cli_invocation', started_at=datetime.now(UTC),
                          duration_ms=120, status='failed'))
        db.add(RequestLog(run_id='older', source_id='weibo_cli', operation='search',
                          unit='cli_invocation', started_at=datetime.now(UTC),
                          duration_ms=120, status='succeeded'))
        db.commit()
        # Read with a new session, like the API process rather than the worker.
        with Session(engine) as reader:
            result = current_progress(reader)
            assert result['stage'] == 'fetch_source_items'
            assert result['sources'][0]['completed'] == 8
            assert result['requests'][0]['count'] == 1
            assert result['requests'][0]['failed'] == 1
        run.status = 'failed'
        run.finished_at = datetime.now(UTC)
        db.commit()
        assert current_progress(db)['sources'][0]['status'] == 'interrupted'
        # Corrupt/missing snapshots cannot break task status visibility.
        progress_path(engine, run.run_id).write_text('broken', encoding='utf-8')
        assert current_progress(db)['status'] == 'failed'
        db.add(AnalysisRun(
            run_id='summary', status='partial', started_at=datetime.now(UTC),
            input_json={'report_window': {'run_ids': ['run-1']}},
        ))
        db.commit()
        assert current_progress(db)['run_id'] == 'run-1'
    engine.dispose()


def test_no_scope_does_not_write_and_idle_has_no_fake_progress(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'idle.db'))
    init_db(engine)
    emit_progress(source='weibo_cli', completed=1)
    with Session(engine) as db:
        assert current_progress(db) == {'status': 'idle', 'sources': [], 'requests': []}
    assert not (tmp_path / 'progress').exists()
    engine.dispose()
