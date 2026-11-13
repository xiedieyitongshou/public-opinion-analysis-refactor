"""RSS fallback scoring, observation persistence and report-time decay regressions."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.session import Base
from app.models import Event, Item, PlatformScore, Source
from app.schemas import (
    CrawlValidationResult,
    FetchSourceItemsOutput,
    PlatformHeatObservation,
    PlatformHeatScore,
    PlatformHeatSignalInput,
    PlatformTrendConfig,
    PlatformTrendInput,
)
from app.services.briefing import platform_sort_key
from app.services.briefing_render import trend_label
from app.services.platform_heat import aggregate_platform_scores, score_weibo_platform
from app.services.platform_trend import analyze_platform_trend
from app.services.platform_trend_assembly import (
    assemble_event_heat_analysis_from_db,
    record_collection_round,
)

END = datetime(2026, 11, 12, 12, tzinfo=UTC)


def rss_signal(**updates):
    values = dict(
        event_id="rss-event", platform="weibo", source_origin="rsshub",
        signal_role="topic_discovery_signal", topic_present=True,
        list_position=5, fetched_at=END.isoformat(),
    )
    values.update(updates)
    return PlatformHeatSignalInput(**values)


@pytest.mark.parametrize(("hours", "count", "expected"), [
    (0, 1, 0.136), (0, 3, 0.168), (0, 5, 0.200),
    (2, 3, 0.084), (4, 3, 0.042), (5, 3, 0.030), (5, 10, 0.035),
])
def test_rss_reference_uses_last_observation_and_capped_count(hours, count, expected):
    score = score_weibo_platform(rss_signal(
        last_topn_seen_at=(END - timedelta(hours=hours)).isoformat(),
        snapshot_presence_count=count,
        published_at=END.isoformat(),
        weibo_signal_status="cli_unavailable",
    ), observed_at=END)
    assert round(score.signal_score, 3) == expected
    assert score.score_basis == "rss_presence_only"
    assert score.score_status == "partial"
    assert score.fallback_reason == "cli_unavailable"
    assert score.raw_metrics_used["rss_observation_count"] == count
    assert score.primary_platform_rank is None
    assert "content_activity_score" not in score.sub_scores


def test_rss_pubdate_and_position_cannot_manufacture_cli_activity():
    recent = score_weibo_platform(rss_signal(published_at=END.isoformat()), observed_at=END)
    old = score_weibo_platform(rss_signal(
        list_position=1, published_at=(END - timedelta(days=30)).isoformat(),
    ), observed_at=END)
    assert recent.signal_score == old.signal_score == pytest.approx(0.136)


def test_zero_accepted_samples_and_unavailable_cli_are_degraded():
    empty = score_weibo_platform(rss_signal(
        matched_status_count=0, published_at=END.isoformat(),
    ), observed_at=END)
    assert empty.score_status == "partial"
    assert empty.fallback_reason == "cli_no_relevant_samples"
    unavailable = score_weibo_platform(rss_signal(
        matched_status_count=10, top_status_like_count=100,
        weibo_signal_status="cli_unavailable",
    ), observed_at=END)
    assert unavailable.score_basis == "rss_presence_only"


def test_missing_observation_time_is_auditable_without_using_rss_pubdate():
    score = score_weibo_platform(rss_signal(
        fetched_at=None, published_at=END.isoformat(),
    ), observed_at=END)
    assert score.signal_score == 0.0
    assert "missing_rss_observation_time" in score.quality_flags


def test_valid_zero_interactions_still_use_cli_formula_and_score_band():
    score = score_weibo_platform(rss_signal(
        matched_status_count=1, top_status_repost_count=0,
        top_status_comment_count=0, top_status_like_count=0,
        latest_status_created_at=None, published_at=END.isoformat(),
    ), observed_at=END)
    assert score.score_status == "ok"
    assert score.score_basis == "cli_samples"
    assert score.signal_score >= 0.30
    # Only the accepted count contributes to content activity when sample time is missing.
    from app.services.platform_heat import normalize_log
    assert score.sub_scores["content_activity_score"] == normalize_log(1, 50)
    assert score.signal_score > score_weibo_platform(rss_signal(
        snapshot_presence_count=100,
    ), observed_at=END).signal_score


def test_duplicate_rss_signals_do_not_add_aggregation_bonus():
    rss = score_weibo_platform(rss_signal(snapshot_presence_count=100), observed_at=END)
    score = aggregate_platform_scores([rss, rss, rss, rss])[0]
    assert score.platform_score == pytest.approx(0.20)
    assert score.score_basis == "rss_presence_only"
    assert score.platform_presence.weibo_topn
    cli = score_weibo_platform(rss_signal(
        matched_status_count=1, top_status_like_count=0,
    ), observed_at=END)
    mixed = aggregate_platform_scores([rss, cli])[0]
    assert mixed.platform_score == cli.signal_score
    assert mixed.score_basis == "cli_samples"


def analyze(observations):
    return analyze_platform_trend(PlatformTrendInput(
        event_id="rss-event", platform="weibo", run_id="report",
        window_start=END - timedelta(hours=24), window_end=END,
        config=PlatformTrendConfig(expected_interval_minutes=60), observations=observations,
    ))


def observation(
    hours, *, run=None, observation_id=None, present=True, status="succeeded", heat=None,
):
    return PlatformHeatObservation(
        event_id="rss-event", platform="weibo", observed_at=END - timedelta(hours=hours),
        run_id=run or f"run-{hours}", observation_id=observation_id or f"rss-{hours}",
        source_id="weibo_rsshub_hot_search", collection_status=status,
        list_complete=status == "succeeded", topn_scope="top20", topn_present=present,
        platform_heat=heat,
    )


def test_report_decay_uses_real_hits_not_latest_attempt_or_saved_score():
    legacy = PlatformHeatScore(
        event_id="rss-event", platform="weibo", platform_score=0.867, score_status="ok",
        sub_scores={"content_activity_score": 1.0},
    )
    history = [observation(7), observation(6), observation(5, heat=legacy)]
    duplicate = observation(5, observation_id="same-run-copy", heat=legacy)
    failed = observation(0, present=None, status="failed")
    result = analyze([*history, duplicate, failed])
    assert result.snapshot_presence_count == 3
    assert result.last_topn_seen_at == END - timedelta(hours=5)
    assert result.last_collection_at == END
    assert result.estimated_platform_heat.platform_score == pytest.approx(0.168 * 2 ** (-2.5))
    assert result.last_observed_heat.platform_score == 0.867
    assert result.trend_status == "unknown"
    repeated = analyze([*history, duplicate, failed])
    assert repeated.estimated_platform_heat == result.estimated_platform_heat
    text = trend_label(SimpleNamespace(platform_heat_analysis=SimpleNamespace(
        platforms={"weibo": result},
    )), "weibo")
    assert "在榜参考分 0.030" in text
    assert "仅RSS依据" in text and "本窗口在榜 3 次" in text
    assert "最后在榜 11-12 15:00" in text
    assert "最后采集尝试：11-12 20:00" in text


def test_sorting_uses_current_estimate_for_old_rss_topics():
    cards = []
    for hours in (5, 0, 4):
        result = analyze([observation(hours)])
        cards.append(SimpleNamespace(
            event_id=f"event-{hours}",
            platform_heat_analysis=SimpleNamespace(platforms={"weibo": result}),
        ))
    ordered = sorted(cards, key=lambda card: platform_sort_key(card, "weibo"))
    assert [card.event_id for card in ordered] == ["event-0", "event-4", "event-5"]


def test_collection_round_persists_real_counts_and_cli_recovery_is_not_a_false_trend():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="微博热搜 RSSHub", source_type="community_hotlist", platform="weibo")
        event = Event(event_id="rss-event", title="Test")
        db.add_all([source, event])
        db.flush()
        db.add(Item(source_id=source.id, event_id=event.id, title="Test", content_hash="same"))
        db.commit()
        for index in range(4):
            at = END - timedelta(hours=3 - index)
            metrics = {} if index < 2 else {
                "matched_status_count": 1, "top_status_like_count": 0 if index == 2 else 5000,
            }
            item = {
                "title": "Test", "content_hash": "same", "platform": "weibo",
                "source_origin": "rsshub", "signal_role": "topic_discovery_signal",
                "fetched_at": "2020-01-01T00:00:00+00:00", "raw_metrics": metrics,
                "normalized": {"topic_present": True, "weibo_signal_status": (
                    "cli_unavailable" if index < 2 else "rsshub_plus_cli"
                )},
            }
            collection = FetchSourceItemsOutput(
                status="succeeded", run_id=f"round-{index}", validate_only=False, dry_run=False,
                requested_source_ids=["weibo_rsshub_hot_search"],
                results=[CrawlValidationResult(
                    source_id="weibo_rsshub_hot_search", run_id=f"round-{index}",
                    status="succeeded", validate_only=False, dry_run=False,
                    observation_id=f"obs-{index}", observed_at=at,
                    list_complete=True, topn_scope="top20", sampling_signature="same",
                    normalized_items=[item, item],
                )],
            )
            for _ in range(2):
                record_collection_round(db, collection=collection, tracked_events=[event])
            score = db.scalar(select(PlatformScore).where(
                PlatformScore.observation_id == f"obs-{index}"
            ))
            detail = score.score_detail_json
            assert detail["score_config_version"].startswith("weibo-cli-rss-fallback-v2:")
            if index < 2:
                assert detail["score_basis"] == "rss_presence_only"
                assert detail["fallback_reason"] == "cli_unavailable"
                assert detail["raw_metrics_used"]["rss_observation_count"] == index + 1
                assert detail["raw_metrics_used"]["rss_last_seen_at"] == at.isoformat()
                assert score.normalized_score == pytest.approx(0.136 if index == 0 else 0.152)
            else:
                assert detail["score_basis"] == "cli_samples" and score.normalized_score >= 0.30
            result = assemble_event_heat_analysis_from_db(
                db, event=event, run_id=f"report-{index}", window_end=at,
                configs={"weibo": PlatformTrendConfig(expected_interval_minutes=60)}, persist=False,
            ).platforms["weibo"]
            assert result.snapshot_presence_count == index + 1
            if index == 2:
                assert result.trend_status == "unknown"
                assert result.estimated_platform_heat is None
            if index == 3:
                assert result.trend_status == "rising"
        assert len(db.scalars(select(PlatformScore)).all()) == 4
