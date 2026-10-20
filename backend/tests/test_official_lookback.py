from datetime import timedelta

import pytest
from test_day43_official_support import BASE_TIME, _event, _official_item
from test_hotspot_analysis import TITLE, register_official_feed, request
from test_hotspot_analysis import environment as environment

from app.models import DailyReport
from app.schemas.display import DailyBriefing
from app.services.briefing_jobs import execute_analysis
from app.services.briefing_render import render_report, render_text
from app.services.official_support import match_official_support
from app.services.report_window import build_window


def test_prior_report_support_does_not_expand_official_attention_window():
    item = _official_item(title=_event()["title"], published_at=BASE_TIME - timedelta(days=3))
    strict = match_official_support(_event(), [item], window_end=BASE_TIME)
    prior = match_official_support(_event(), [item], window_end=BASE_TIME, allow_prior_reports=True)
    assert strict.official_support_status == "not_found"
    assert prior.official_support_status == "supported"
    assert (
        prior.official_support_detail.support_window["start"]
        != (prior.official_support_detail.attention_window["start"])
    )


@pytest.mark.parametrize("age", [8, -1, None])
def test_expired_future_or_undated_never_support(age):
    stamp = BASE_TIME - timedelta(days=age) if age is not None else None
    item = _official_item(title=_event()["title"], published_at=stamp)
    result = match_official_support(
        _event(), [item], window_end=BASE_TIME, allow_prior_reports=True
    )
    assert result.official_support_status == "not_found"


def test_report_before_explicit_event_time_rejected():
    event = _event()
    event["event_detail"]["event_time_hint"] = BASE_TIME.isoformat()
    item = _official_item(title=event["title"], published_at=BASE_TIME - timedelta(days=3))
    result = match_official_support(event, [item], window_end=BASE_TIME, allow_prior_reports=True)
    assert result.official_support_status == "not_found"
    assert result.official_support_detail.freshness_exclusions[0]["reason"] == (
        "official_report_predates_event"
    )


def test_previous_reporting_classifies_d_but_not_official_column(environment, monkeypatch):
    db, clock, _ = environment
    clock.titles = [TITLE]
    register_official_feed(environment, monkeypatch, published_at=clock.time - timedelta(days=2))
    run = execute_analysis(
        db, request("lookback", clock.time, sources=["zhihu_hot_list", "chinanews_scroll_rss"])
    )
    assert run.status in ("succeeded", "partial")
    report_id = build_window(
        db,
        start=clock.time - timedelta(seconds=1),
        end=clock.time + timedelta(minutes=1),
        run_ids=[run.run_id],
        run_id="lookback-summary",
    )
    report = db.get(DailyReport, report_id)
    briefing = DailyBriefing.model_validate(report.briefing_json)
    zhihu = next(s for s in briefing.sections if s.key == "zhihu")
    official = next(s for s in briefing.sections if s.key == "official")
    assert zhihu.event_cards[0].priority_category == "D_single_platform_with_official"
    assert any(c.is_prior_report for c in zhihu.event_cards[0].source_citations)
    assert not official.event_cards
    assert "此前报道" in render_report(briefing)
    assert "此前报道" in render_text(briefing)
    assert not report.quality_review_json["blocks_publish"]
