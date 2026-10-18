from copy import deepcopy
from datetime import timedelta

from test_hotspot_analysis import OTHER_TITLE, TITLE, request
from test_hotspot_analysis import environment as environment
from test_overnight_test import NOW, finish
from test_overnight_test import db as db

from app.models import DailyReport
from app.models.briefing import AnalysisRun
from app.schemas.collection_session import CollectionSessionConfig
from app.services import overnight_test as night
from app.services.briefing_jobs import execute_analysis
from app.services.report_window import build_window, rebuild_window


def test_custom_interval_and_no_report(db):
    night.start(
        db,
        NOW,
        config=CollectionSessionConfig(
            duration_hours=2, interval_minutes=30, generate_report=False
        ),
    )
    for slot in range(5):
        at = NOW + timedelta(minutes=30 * slot)
        night.tick(db, at)
        finish(db)
        night.tick(db, at + timedelta(seconds=1))
    state = night.describe(db)
    assert state["planned_rounds"] == 5 and state["status"] == "completed"
    assert state["report_id"] is None and state["error"] is None


def test_continuous_requires_explicit_stop(db):
    night.start(db, NOW, config=CollectionSessionConfig(duration_hours=None, generate_report=False))
    night.tick(db, NOW + timedelta(hours=9))
    finish(db)
    assert night.describe(db)["planned_rounds"] is None
    assert night.current(db).status == "running"
    night.stop(db)
    night.tick(db, NOW + timedelta(hours=9, minutes=1))
    assert night.current(db).status == "stopped"


def test_window_excludes_history_reuses_observed_citations_and_survives_rebuild(environment):
    db, clock, _ = environment
    execute_analysis(db, request("history", clock.time))
    clock.time += timedelta(hours=2)
    start = clock.time - timedelta(seconds=1)
    clock.titles = [TITLE]
    execute_analysis(db, request("selected-1", clock.time))
    clock.time += timedelta(hours=1)
    execute_analysis(db, request("selected-2", clock.time))
    report_id = build_window(
        db,
        start=start,
        end=clock.time + timedelta(minutes=1),
        run_ids=["selected-1", "selected-2"],
        run_id="test-window",
    )
    before = deepcopy(db.get(DailyReport, report_id).briefing_json)
    titles = {c["title"] for s in before["sections"] for c in s["event_cards"]}
    assert titles == {TITLE} and OTHER_TITLE not in titles
    card = before["sections"][0]["event_cards"][0]
    assert card["platform_heat_analysis"]["platforms"]["zhihu"]["snapshot_presence_count"] == 2
    assert card["platform_heat_analysis"]["platforms"]["zhihu"]["trend_status"] != "unknown"
    run = db.get(AnalysisRun, "test-window")
    assert run.input_json["trend_configs"]["zhihu"]["expected_interval_minutes"] == 60
    updated = rebuild_window(db, run)
    after = db.get(DailyReport, updated).briefing_json
    assert after["window_start"] == before["window_start"]
    assert (
        after["sections"][0]["event_cards"][0]["platform_heat_analysis"]["platforms"]
        == card["platform_heat_analysis"]["platforms"]
    )
    assert not db.get(DailyReport, updated).quality_review_json["blocks_publish"]


def test_latest_report_link_follows_revision(db):
    row = night.start(db, NOW)
    row.status, row.report_id = "completed", 1
    db.add(
        AnalysisRun(
            run_id=f"overnight-{row.token}-summary",
            started_at=NOW,
            input_json={},
            status="partial",
            report_id=2,
        )
    )
    db.commit()
    assert night.describe(db)["report_id"] == 2
