from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from test_hotspot_analysis import OTHER_TITLE, TITLE, request
from test_hotspot_analysis import environment as environment

from app.db.init_db import init_db
from app.models.briefing import AnalysisRun
from app.services import overnight_test as night

NOW = datetime(2026, 10, 18, 14, tzinfo=UTC)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool,
                           connect_args={'check_same_thread': False})
    init_db(engine)
    monkeypatch.setattr(night, 'preflight', lambda db: None)
    with Session(engine) as session:
        yield session
    engine.dispose()


def finish(db):
    run = db.scalar(select(AnalysisRun).where(AnalysisRun.status == 'queued'))
    run.status = 'failed'
    run.error = 'test failure'
    db.commit()
    return run


def test_nine_hourly_slots_no_duplicates_and_no_more_after_end(db, monkeypatch):
    row = night.start(db, NOW)
    token = row.token
    assert night.start(db, NOW).token == token
    calls = []
    monkeypatch.setattr(night, 'summarize', lambda *args: calls.append(1) or 42)
    for hour in range(9):
        at = NOW + timedelta(hours=hour, seconds=2)
        assert night.tick(db, at)
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.status == 'queued'))
        assert run.input_json['trend_configs']['weibo']['expected_interval_minutes'] == 60
        from app.core.config import settings
        assert run.input_json['official_search_enabled'] == settings.official_search_enabled
        assert run.input_json['collection']['source_ids'] == [
            'zhihu_hot_list', 'weibo_rsshub_hot_search',
            'chinanews_scroll_rss', 'people_politics_rss', 'xinhua_politics_rss',
        ]
        night.tick(db, at)
        assert len(night.current(db).rounds_json) == hour + 1
        finish(db)
        night.tick(db, at + timedelta(minutes=3))
    assert night.current(db).status == 'completed'
    assert calls == [1]
    assert night.tick(db, NOW + timedelta(hours=12)) is False


def test_restart_skips_missed_hours_instead_of_burst(db):
    night.start(db, NOW)
    night.tick(db, NOW + timedelta(hours=3, minutes=10))
    slots = night.current(db).rounds_json
    assert len(slots) == 4
    assert all(s.get('error') == 'missed_hour_after_delay' for s in slots[:3])
    assert slots[3]['run_id'].endswith('-3')


def test_future_start_waits_then_runs_exactly_five_45_minute_slots(db, monkeypatch):
    from app.schemas.collection_session import CollectionSessionConfig

    start = NOW + timedelta(minutes=30)
    row = night.start(
        db, NOW, CollectionSessionConfig(duration_hours=3, interval_minutes=45, start_at=start),
    )
    assert night.as_utc(row.started_at) == start
    assert row.config_json['scheduled_start'] is True
    db.expunge_all()
    row = night.current(db)
    summaries = []
    monkeypatch.setattr(night, 'summarize', lambda *args: summaries.append(1) or 42)
    assert night.tick(db, NOW)
    assert not list(db.scalars(select(AnalysisRun)))
    for slot in range(5):
        due = start + timedelta(minutes=45 * slot)
        night.tick(db, due - timedelta(seconds=1))
        assert db.scalar(select(AnalysisRun).where(AnalysisRun.status == 'queued')) is None
        night.tick(db, due + timedelta(seconds=1))
        assert len(night.current(db).rounds_json) == slot + 1
        run = finish(db)
        assert run.run_id.endswith(f'-{slot}')
    night.tick(db, start + timedelta(hours=3, minutes=3))
    assert row.status == 'completed' and summaries == [1]
    assert len(list(db.scalars(select(AnalysisRun)))) == 5
    assert not night.tick(db, start + timedelta(hours=4))


def test_explicit_scheduled_start_never_catches_up_after_its_start_grace(db):
    row = night.start(db, NOW)
    row.config_json = {**row.config_json, 'scheduled_start': True}
    db.commit()
    night.tick(db, NOW + timedelta(minutes=6))
    assert row.status == 'failed' and '错过预约' in row.error
    assert not list(db.scalars(select(AnalysisRun)))


def test_scheduled_start_rejects_datetime_without_timezone():
    from app.schemas.collection_session import CollectionSessionConfig

    with pytest.raises(ValueError, match='必须包含时区'):
        CollectionSessionConfig(start_at=NOW.replace(tzinfo=None))


@pytest.mark.parametrize('start_at', [NOW, NOW - timedelta(minutes=1)])
def test_past_scheduled_start_never_runs_preflight_or_creates_task(db, monkeypatch, start_at):
    from app.schemas.collection_session import CollectionSessionConfig

    calls = []
    monkeypatch.setattr(night, 'preflight', lambda db: calls.append(1))
    with pytest.raises(ValueError, match='必须晚于当前时间'):
        night.start(db, NOW, CollectionSessionConfig(start_at=start_at))
    assert calls == [] and night.current(db) is None
    assert not list(db.scalars(select(AnalysisRun)))


def test_schedule_expiring_during_preflight_is_rejected(db, monkeypatch):
    from app.schemas.collection_session import CollectionSessionConfig

    clock = iter([100.0, 220.0])
    monkeypatch.setattr(night, 'monotonic', lambda: next(clock))
    with pytest.raises(ValueError, match='启动检查期间已错过预约时间'):
        night.start(db, NOW, CollectionSessionConfig(start_at=NOW + timedelta(minutes=1)))
    assert night.current(db) is None
    assert not list(db.scalars(select(AnalysisRun)))


def test_cancel_future_reservation_never_collects(db):
    from app.schemas.collection_session import CollectionSessionConfig

    row = night.start(db, NOW, CollectionSessionConfig(start_at=NOW + timedelta(hours=1)))
    night.stop(db)
    night.tick(db, NOW + timedelta(seconds=1))
    assert row.status == 'stopped' and row.rounds_json == [] and row.report_id is None
    assert not list(db.scalars(select(AnalysisRun)))


def test_schedule_api_accepts_local_offset_and_rejects_past_or_naive_times(db):
    from fastapi.testclient import TestClient

    from app.db.session import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    headers = {'Authorization': 'Bearer test-admin'}
    try:
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        assert client.post('/api/overnight-test', headers=headers,
                           json={'start_at': past}).status_code == 409
        naive = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None).isoformat()
        assert client.post('/api/overnight-test', headers=headers,
                           json={'start_at': naive}).status_code == 422
        future = datetime.now(UTC) + timedelta(hours=1)
        local = future.astimezone(timezone(timedelta(hours=8)))
        response = client.post('/api/overnight-test', headers=headers, json={
            'start_at': local.isoformat(), 'duration_hours': 3, 'interval_minutes': 45,
        })
        assert response.status_code == 202
        data = response.json()
        assert data['planned_rounds'] == 5 and data['rounds'] == []
        assert night.as_utc(data['started_at']) == future
        assert not list(db.scalars(select(AnalysisRun)))
    finally:
        client.close()
        app.dependency_overrides.pop(get_db, None)


def test_stop_waits_for_current_round_then_summarizes(db, monkeypatch):
    night.start(db, NOW)
    night.tick(db, NOW)
    night.stop(db)
    night.tick(db, NOW + timedelta(minutes=1))
    assert night.current(db).status == 'stopping'
    finish(db)
    monkeypatch.setattr(night, 'summarize', lambda *args: 7)
    night.tick(db, NOW + timedelta(minutes=2))
    assert night.current(db).status == 'stopped'
    assert night.current(db).report_id == 7


def test_all_failed_has_no_fake_report(db):
    row = night.start(db, NOW)
    night.tick(db, NOW)
    finish(db)
    night.stop(db)
    night.tick(db, NOW + timedelta(minutes=1))
    assert row.report_id is None
    assert row.error


def test_preflight_failure_does_not_start(db, monkeypatch):
    def fail(db):
        raise ValueError('IP_GEO_DENIED')
    monkeypatch.setattr(night, 'preflight', fail)
    with pytest.raises(ValueError, match='IP_GEO_DENIED'):
        night.start(db, NOW)
    assert night.current(db) is None


def test_final_report_includes_earlier_event_without_sending_or_round_drafts(environment):
    from app.models import DailyReport
    from app.models.briefing import EmailDelivery, OvernightTest
    from app.services.briefing_jobs import execute_analysis
    db, clock, _ = environment
    trial = OvernightTest(id=1, token='offline', status='running', started_at=clock.time,
                          ends_at=clock.time + timedelta(hours=8), rounds_json=[
                              {'slot':0,'run_id':'overnight-offline-0'},
                              {'slot':1,'run_id':'overnight-offline-1'},
                          ])
    db.add(trial)
    db.commit()
    first = execute_analysis(db, request('overnight-offline-0', clock.time))
    assert first.status in ('partial','succeeded')
    assert first.report_id is None
    clock.time += timedelta(hours=1)
    clock.titles = [TITLE]
    second = execute_analysis(db, request('overnight-offline-1', clock.time))
    assert second.report_id is None
    report_id = night.summarize(db, trial, clock.time + timedelta(minutes=1))
    report = db.get(DailyReport, report_id)
    titles = {card['title'] for s in report.briefing_json['sections'] for card in s['event_cards']}
    assert TITLE in titles and OTHER_TITLE in titles
    assert report.status == 'draft', report.quality_review_json
    assert len(list(db.scalars(select(DailyReport)))) == 1
    assert not list(db.scalars(select(EmailDelivery)))
