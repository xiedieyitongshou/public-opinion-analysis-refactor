from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.init_db import init_db
from app.models.briefing import AnalysisRun
from app.services import overnight_test as night
from test_hotspot_analysis import environment as environment, request, TITLE, OTHER_TITLE

NOW = datetime(2026, 10, 18, 14, tzinfo=UTC)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine('sqlite://', poolclass=StaticPool)
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
        assert run.input_json['official_search_enabled'] is False
        assert run.input_json['collection']['source_ids'] == ['zhihu_hot_list', 'zhihu_search', 'weibo_rsshub_hot_search']
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
    from app.models.briefing import OvernightTest, EmailDelivery
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
