"""Replays and review revisions must keep the original data collection dates."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.briefing import report_history, report_info
from app.db.session import Base
from app.models import DailyReport
from app.models.briefing import AnalysisRun, ReportRevision

START = datetime(2026, 10, 31, tzinfo=UTC)
GENERATED = datetime(2026, 11, 3, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def capture(db, key, observed, source="zhihu_hot_list", *, with_cli=False):
    item = {"fetched_at": observed.isoformat(), "raw_payload": {}}
    if with_cli:
        item["raw_payload"] = {"cli_enrichment": {"samples": [{"text": "微博正文"}]}}
    row = AnalysisRun(
        run_id=key, status="partial", started_at=GENERATED,
        input_json={}, collection_json={"results": [{
            "source_id": source, "source_name": source, "observed_at": observed.isoformat(),
            "normalized_items": [item],
        }]},
    )
    db.add(row)
    db.flush()
    return row


def report(db, key, *, children=None, supersedes=None, window_start=START):
    if children is not None and db.get(AnalysisRun, key) is None:
        db.add(AnalysisRun(run_id=key, status="partial", started_at=GENERATED,
                           input_json={"report_window": {"run_ids": children}}))
    row = DailyReport(
        report_date=date(2026, 10, 31), title="旧数据新版本", created_at=GENERATED,
        briefing_json={"run_id": key, "window_start": window_start.isoformat(),
                       "window_end": (START + timedelta(hours=2)).isoformat()},
    )
    db.add(row)
    db.flush()
    db.add(ReportRevision(report_id=row.id, run_id=key, fingerprint=str(row.id),
                          supersedes_id=supersedes))
    db.flush()
    return row


def test_replay_uses_original_observations_not_november_processing_time(db):
    first = START + timedelta(minutes=5)
    last = START + timedelta(minutes=50)
    capture(db, "r0", first)
    capture(db, "r1", last, "weibo_rsshub_hot_search", with_cli=True)
    capture(db, "unrelated-latest", GENERATED)
    row = report(db, "summary", children=["r0", "r1"])
    data = report_info(db, row)
    meta = data["collection_provenance"]
    assert meta["first_observed_at"] == first and meta["last_observed_at"] == last
    assert meta["round_count"] == 2 and meta["complete"]
    assert meta["platform_last_collection_at"] == {"zhihu": first, "weibo": last}
    assert {s["source_id"] for s in meta["sources"]} == {
        "zhihu_hot_list", "weibo_rsshub_hot_search", "weibo_cli",
    }
    assert data["created_at"] == GENERATED
    assert data["window_start"] == START.isoformat()


def test_history_keeps_same_capture_dates_with_distinct_version_times(db):
    capture(db, "r0", START + timedelta(minutes=5))
    old = report(db, "summary", children=["r0"])
    old.created_at = GENERATED - timedelta(days=1)
    new = report(db, "summary", children=["r0"], supersedes=old.id)
    previous = report_history(db, new.id)[0]
    current = report_info(db, new)
    assert previous["created_at"] != current["created_at"]
    assert previous["collection_provenance"] == current["collection_provenance"]


def test_missing_constituent_capture_is_explicitly_partial(db):
    capture(db, "r0", START + timedelta(minutes=5))
    row = report(db, "summary", children=["r0", "missing"])
    meta = report_info(db, row)["collection_provenance"]
    assert meta["round_count"] == 1 and meta["expected_round_count"] == 2
    assert not meta["complete"]


def test_collection_dates_and_sources_respect_custom_report_window(db):
    capture(db, "outside", START - timedelta(minutes=5))
    capture(db, "inside", START + timedelta(minutes=5), "weibo_rsshub_hot_search")
    row = report(db, "summary", children=["outside", "inside"])
    meta = report_info(db, row)["collection_provenance"]
    assert meta["first_observed_at"] == START + timedelta(minutes=5)
    assert [s["source_id"] for s in meta["sources"]] == ["weibo_rsshub_hot_search"]


def test_legacy_citation_fallback_uses_fetch_not_article_publication(db):
    row = report(db, "legacy")
    row.source_citations_json = [{
        "source_name": "中新网", "fetched_at": (START + timedelta(minutes=5)).isoformat(),
        "published_at": "2026-10-01T00:00:00Z",
    }]
    meta = report_info(db, row)["collection_provenance"]
    assert meta["first_observed_at"] == START + timedelta(minutes=5)
    assert meta["origin"] == "report_citations" and not meta["complete"]


def test_legacy_report_does_not_invent_collection_dates_from_report_window(db):
    row = report(db, "legacy")
    meta = report_info(db, row)["collection_provenance"]
    assert meta["first_observed_at"] is None and meta["last_observed_at"] is None
    assert meta["origin"] == "unrecorded"
    assert meta["platform_last_collection_at"] == {}


def test_last_platform_check_includes_empty_or_failed_capture_but_not_other_sources(db):
    capture(db, "first", START + timedelta(minutes=5))
    late = START + timedelta(minutes=50)
    run = capture(db, "last", late)
    run.collection_json = {"results": [{
        "source_id": "zhihu_hot_list", "observed_at": late.isoformat(),
        "status": "failed", "normalized_items": [], "normalized_count": 0,
    }]}
    capture(db, "official", START + timedelta(minutes=55), "chinanews_scroll_rss")
    row = report(db, "summary", children=["first", "last", "official"])
    times = report_info(db, row)["collection_provenance"]["platform_last_collection_at"]
    assert times == {"zhihu": late}
