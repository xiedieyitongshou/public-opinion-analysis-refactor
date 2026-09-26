from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.db.init_db import init_db
from app.db.session import Base
from app.models import Event, EventSnapshot, Item, PlatformScore, Source
from app.schemas import (
    AnalyzeEventHeatInput,
    CrawlValidationResult,
    EventCard,
    EvidenceSummary,
    FetchSourceItemsOutput,
    PlatformHeatObservation,
    PlatformHeatScore,
    PlatformTrendConfig,
    PlatformTrendInput,
)
from app.services.classification_assembly import assemble_hotspot_classification_from_db
from app.services.platform_trend import analyze_platform_trend
from app.services.platform_trend_assembly import (
    assemble_event_heat_analysis_from_db,
    current_event_heat_analysis,
    rank_events_within_platform,
    record_collection_round,
    record_platform_observations,
)
from app.tools.default_tools import analyze_event_heat_handler, build_default_tool_registry
from app.tools.runtime import ToolContext

END = datetime(2026, 9, 26, 16, tzinfo=UTC)
START = END - timedelta(hours=24)
CONFIG = PlatformTrendConfig(expected_interval_minutes=60)


def observation(
    platform: str,
    hour: int,
    *,
    present: bool | None = True,
    rank: int | None = None,
    score: float | None = None,
    status: str = "succeeded",
    complete: bool = True,
    observation_id: str | None = None,
    scope: str = "top20:skip=0",
    metrics: dict | None = None,
    config_version: str | None = "v1",
    sampling: str | None = "same-limit-and-query",
    components: str | None = "same-weights",
) -> PlatformHeatObservation:
    observed_at = datetime(2026, 9, 26, hour, tzinfo=UTC)
    heat = None
    if score is not None:
        heat = PlatformHeatScore(
            event_id="event-1",
            platform=platform,
            platform_score=score,
            score_status="ok",
            primary_platform_rank=rank,
            raw_metrics_used=metrics or {},
        )
    return PlatformHeatObservation(
        event_id="event-1",
        platform=platform,
        observation_id=observation_id or f"{platform}-{hour}",
        run_id=f"source-run-{hour}",
        observed_at=observed_at,
        source_id="zhihu_hot_list" if platform == "zhihu" else "weibo_rsshub_hot_search",
        collection_status=status,
        list_complete=complete,
        topn_scope=scope,
        topn_present=present,
        rank=rank,
        platform_heat=heat,
        score_config_version=config_version,
        sampling_signature=sampling,
        score_component_signature=components,
    )


def analyze(platform: str, observations: list[PlatformHeatObservation], *, end=END):
    return analyze_platform_trend(PlatformTrendInput(
        event_id="event-1",
        platform=platform,
        run_id="analysis-run",
        window_start=end - timedelta(hours=24),
        window_end=end,
        observations=observations,
        config=CONFIG,
    ))


def test_window_dedup_utc_and_no_duration_extrapolation() -> None:
    end = datetime(2026, 9, 26, 10, tzinfo=UTC)
    at_start = observation("zhihu", 10, observation_id="start")
    at_start.observed_at = end - timedelta(hours=24)
    future = observation("zhihu", 11)
    first = observation("zhihu", 8, rank=10, score=0.3)
    last = observation("zhihu", 10, rank=5, score=0.6)
    last.observed_at = last.observed_at.astimezone(timezone(timedelta(hours=8)))
    result = analyze("zhihu", [at_start, first, last, last, future], end=end)
    assert result.snapshot_presence_count == 2
    assert result.first_topn_seen_at == first.observed_at
    assert result.continuous_topn_minutes == 120
    assert result.duration_status == "ok"
    assert result.trend_status == "rising"
    assert result.trend_basis == "rank"
    assert result.rank_delta == 5
    assert result.comparison_observation_ids == ["zhihu-8", "zhihu-10"]


@pytest.mark.parametrize(
    ("ranks", "expected"),
    [((10, 5), "rising"), ((5, 10), "cooling"), ((5, 5), "stable")],
)
def test_zhihu_real_rank_trend(ranks: tuple[int, int], expected: str) -> None:
    result = analyze("zhihu", [
        observation("zhihu", 15, rank=ranks[0]),
        observation("zhihu", 16, rank=ranks[1]),
    ])
    assert result.trend_status == expected


def test_absence_restart_failed_round_and_stale_round() -> None:
    end = datetime(2026, 9, 26, 14, tzinfo=UTC)
    history = [
        observation("zhihu", 8, rank=8),
        observation("zhihu", 10, rank=6),
        observation("zhihu", 12, present=False),
        observation("zhihu", 14, rank=4),
    ]
    result = analyze("zhihu", history, end=end)
    assert result.snapshot_presence_count == 3
    assert result.continuous_topn_minutes == 0
    assert result.duration_status == "partial"
    assert result.trend_status == "unknown"
    absent = analyze("zhihu", history[:-1], end=datetime(2026, 9, 26, 12, tzinfo=UTC))
    assert absent.current_topn_present is False
    assert absent.continuous_topn_minutes == 0
    assert absent.duration_status == "ok"
    failed = analyze("zhihu", history[:2] + [
        observation("zhihu", 14, present=None, status="failed", complete=False)
    ], end=end)
    assert failed.continuous_topn_minutes is None
    assert failed.latest_platform_heat is None
    assert failed.trend_status == "unknown"
    assert "source_unavailable" in failed.quality_flags
    stale = analyze("zhihu", history[:2], end=end)
    assert stale.continuous_topn_minutes is None
    assert "stale_observation" in stale.quality_flags


def test_never_seen_on_main_list_has_no_duration_even_when_absence_is_confirmed() -> None:
    result = analyze("zhihu", [observation("zhihu", 16, present=False)])
    assert result.current_topn_present is False
    assert result.continuous_topn_minutes is None
    assert result.duration_status == "unknown"
    assert "no_topn_evidence" in result.quality_flags


def test_unknown_and_gap_cut_continuity_without_skipping_history() -> None:
    result = analyze("zhihu", [
        observation("zhihu", 12, rank=9),
        observation("zhihu", 14, present=None, complete=False),
        observation("zhihu", 16, rank=5),
    ])
    assert result.continuous_topn_minutes == 0
    assert result.trend_status == "unknown"
    gap = analyze("zhihu", [
        observation("zhihu", 12, rank=9),
        observation("zhihu", 16, rank=5),
    ])
    assert gap.continuous_topn_minutes == 0
    assert "observation_gap" in gap.quality_flags


def test_incomplete_hit_is_counted_but_cannot_establish_continuity_or_trend() -> None:
    result = analyze("zhihu", [
        observation("zhihu", 15, rank=10),
        observation("zhihu", 16, rank=5, complete=False),
    ])
    assert result.snapshot_presence_count == 2
    assert result.continuous_topn_minutes == 0
    assert result.duration_status == "partial"
    assert result.trend_status == "unknown"
    assert "incomplete_list" in result.quality_flags


def test_weibo_rss_only_is_not_a_rank_or_score_trend() -> None:
    result = analyze("weibo", [
        observation("weibo", 15, score=0.4),
        observation("weibo", 16, score=0.6),
    ])
    assert result.continuous_topn_minutes == 60
    assert result.trend_status == "unknown"
    assert result.rank_delta is None
    assert "insufficient_trend_metrics" in result.quality_flags


@pytest.mark.parametrize(
    ("scores", "expected"),
    [((0.3, 0.6), "rising"), ((0.6, 0.3), "cooling"), ((0.4, 0.42), "stable")],
)
def test_weibo_comparable_cli_scores(scores: tuple[float, float], expected: str) -> None:
    result = analyze("weibo", [
        observation("weibo", 15, score=scores[0], metrics={
            "matched_status_count": 4, "weibo_search_total_number_proxy": 100,
        }),
        observation("weibo", 16, score=scores[1], metrics={
            "matched_status_count": 8, "weibo_search_total_number_proxy": 200,
        }),
    ])
    assert result.trend_status == expected
    assert result.trend_basis == "platform_score"
    assert result.rank_delta is None


def test_weibo_incomparable_sampling_and_freshness_only() -> None:
    first = observation("weibo", 15, score=0.4, metrics={"matched_status_count": 4})
    changed = observation(
        "weibo", 16, score=0.8, metrics={"matched_status_count": 8}, sampling="new-limit"
    )
    result = analyze("weibo", [first, changed])
    assert result.trend_status == "unknown"
    assert "incomparable_scores" in result.quality_flags
    freshness = observation("weibo", 16, score=0.6, metrics={"matched_status_count": 4})
    result = analyze("weibo", [first, freshness])
    assert result.trend_status == "unknown"


def test_missing_sampling_config_preserves_only_static_heat() -> None:
    data = PlatformTrendInput(
        event_id="event-1", platform="zhihu", run_id="r", window_start=START,
        window_end=END, observations=[observation("zhihu", 16, rank=2, score=0.9)],
    )
    result = analyze_platform_trend(data)
    assert result.latest_platform_heat is not None
    assert result.continuous_topn_minutes is None
    assert "missing_sampling_config" in result.quality_flags


def test_snapshot_upsert_analysis_and_classification_are_independent() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        event = Event(event_id="event-1", title="Test")
        db.add(event)
        db.commit()
        event.event_detail_json = {
            "priority_category": "E_single_platform_only",
            "classification_detail": {
                "run_id": "analysis-run", "window_start": START.isoformat(),
                "window_end": END.isoformat(),
            },
        }
        zhihu = observation("zhihu", 16, rank=3, score=0.7)
        weibo = observation("weibo", 16, score=0.5)
        assert record_platform_observations(db, event=event, observations=[zhihu, weibo]) == 2
        assert record_platform_observations(db, event=event, observations=[zhihu]) == 1
        rows = db.scalars(select(EventSnapshot)).all()
        assert len(rows) == 1
        assert len(rows[0].metrics_json["platform_observations"]["zhihu"]) == 1
        assert len(rows[0].metrics_json["platform_observations"]["weibo"]) == 1
        assert len(db.scalars(select(PlatformScore)).all()) == 2
        first = assemble_event_heat_analysis_from_db(
            db, event=event, run_id="analysis-run",
            configs={"zhihu": CONFIG, "weibo": CONFIG},
        )
        assert first.platforms["zhihu"].snapshot_presence_count == 1
        assert first.platforms["weibo"].snapshot_presence_count == 1
        assert current_event_heat_analysis(event) is not None, event.event_detail_json
        assert event.event_detail_json["classification_detail"]["run_id"] == "analysis-run"
        second = assemble_event_heat_analysis_from_db(
            db, event=event, run_id="analysis-run",
            configs={"zhihu": CONFIG, "weibo": CONFIG},
        )
        assert second.platforms["zhihu"].snapshot_presence_count == 1
        assert len(db.scalars(select(EventSnapshot)).all()) == 1
        assert rank_events_within_platform(
            [event], platform="zhihu", category="E_single_platform_only"
        ) == [event]
        with pytest.raises(ValueError, match="reuse"):
            assemble_event_heat_analysis_from_db(
                db, event=event, run_id="analysis-run", window_end=END + timedelta(hours=1)
            )
        event.event_detail_json = {**event.event_detail_json, "classification_detail": {
            "run_id": "other", "window_start": START.isoformat(), "window_end": END.isoformat()
        }}
        assert current_event_heat_analysis(event) is None


def test_collection_round_retains_repeated_real_observations_after_item_dedup() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="知乎热榜", source_type="community_question_hotlist", platform="zhihu")
        event = Event(event_id="event-1", title="Test")
        db.add_all([source, event])
        db.flush()
        db.add(Item(
            source_id=source.id, event_id=event.id, title="Test", content_hash="same-content"
        ))
        db.commit()
        item = {
            "content_hash": "same-content", "title": "Test", "platform": "zhihu",
            "source_origin": "official_api", "signal_role": "attention_signal",
            "rank": 5, "fetched_at": END.isoformat(), "raw_metrics": {"vote_count": 10},
        }
        for run, hour in (("source-a", 15), ("source-b", 16)):
            collection = FetchSourceItemsOutput(
                status="succeeded", run_id=run, validate_only=False, dry_run=False,
                requested_source_ids=["zhihu_hot_list"],
                results=[CrawlValidationResult(
                    source_id="zhihu_hot_list", run_id=run, status="succeeded",
                    validate_only=False, dry_run=False, observation_id=f"{run}:zhihu_hot_list",
                    observed_at=datetime(2026, 9, 26, hour, tzinfo=UTC),
                    list_complete=True, topn_scope="top20:skip=0",
                    sampling_signature="same", normalized_items=[item],
                )],
            )
            assert record_collection_round(
                db, collection=collection, tracked_events=[event]
            ) == 1
            assert record_collection_round(
                db, collection=collection, tracked_events=[event]
            ) == 1
        analysis = assemble_event_heat_analysis_from_db(
            db, event=event, run_id="analysis-run", window_end=END,
            configs={"zhihu": CONFIG, "weibo": CONFIG},
        )
        assert analysis.platforms["zhihu"].snapshot_presence_count == 2
        assert analysis.platforms["zhihu"].continuous_topn_minutes == 60
        assert len(db.scalars(select(PlatformScore)).all()) == 2
        assert len(db.scalars(select(Item)).all()) == 1
        classification = assemble_hotspot_classification_from_db(
            run_id="analysis-run", event=event, db=db, window_end=END, persist=False,
        )
        assert classification.classification.platform_presence.zhihu_topn is True


def test_collection_round_complete_absence_scope_change_and_dry_run() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        event = Event(event_id="event-1", title="Tracked")
        db.add(event)
        db.commit()

        def round_at(hour: int, *, scope: str, complete: bool, dry_run: bool = False):
            run = f"run-{hour}"
            return FetchSourceItemsOutput(
                status="succeeded", run_id=run, validate_only=False, dry_run=dry_run,
                requested_source_ids=["zhihu_hot_list"],
                results=[CrawlValidationResult(
                    source_id="zhihu_hot_list", run_id=run, status="succeeded",
                    validate_only=False, dry_run=dry_run,
                    observation_id=f"{run}:zhihu_hot_list",
                    observed_at=datetime(2026, 9, 26, hour, tzinfo=UTC),
                    list_complete=complete, topn_scope=scope, normalized_items=[],
                )],
            )

        assert record_collection_round(
            db, collection=round_at(13, scope="top20", complete=True, dry_run=True),
            tracked_events=[event],
        ) == 0
        assert record_collection_round(
            db, collection=round_at(14, scope="top20", complete=True),
            tracked_events=[event],
        ) == 1
        assert record_collection_round(
            db, collection=round_at(15, scope="top10", complete=True),
            tracked_events=[event],
        ) == 1
        assert record_collection_round(
            db, collection=round_at(16, scope="top10", complete=False),
            tracked_events=[event],
        ) == 1
        recorded = sorted(
            [entry for row in db.scalars(select(EventSnapshot)).all()
             for entry in row.metrics_json["platform_observations"]["zhihu"]],
            key=lambda item: item["observed_at"],
        )
        assert [item["topn_present"] for item in recorded] == [False, None, None]
        assert "incomparable_scope" in recorded[1]["quality_flags"]
        assert "incomplete_list" in recorded[2]["quality_flags"]


def test_weibo_rss_position_is_never_saved_as_official_rank() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        event = Event(event_id="event-1", title="Weibo")
        db.add(event)
        db.commit()
        collection = FetchSourceItemsOutput(
            status="succeeded", run_id="collection-1", validate_only=False,
            dry_run=False, requested_source_ids=["weibo_rsshub_hot_search"],
            results=[CrawlValidationResult(
                source_id="weibo_rsshub_hot_search", run_id="collection-1",
                status="succeeded", validate_only=False, dry_run=False,
                observation_id="weibo-collection-1", observed_at=END,
                list_complete=True, topn_scope="top20:skip=0",
                sampling_signature="same", normalized_items=[{
                    "content_hash": "weibo-topic", "title": "Topic", "platform": "weibo",
                    "source_origin": "rsshub", "signal_role": "topic_discovery_signal",
                    "raw_metrics": {"list_position": 2},
                    "normalized": {"topic_present": True},
                    "fetched_at": END.isoformat(),
                }],
            )],
        )
        record_collection_round(
            db, collection=collection, tracked_events=[event],
            event_ids_by_content_hash={"weibo-topic": "event-1"},
        )
        analysis = assemble_event_heat_analysis_from_db(
            db, event=event, run_id="analysis-run", window_end=END,
            configs={"zhihu": CONFIG, "weibo": CONFIG}, persist=False,
        )
        weibo = analysis.platforms["weibo"]
        assert weibo.current_topn_present is True
        assert weibo.latest_platform_heat.primary_platform_rank is None
        assert weibo.trend_status == "unknown"


def test_search_only_score_is_retained_without_topn_duration() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        event = Event(event_id="event-1", title="Search")
        db.add(event)
        db.commit()
        collection = FetchSourceItemsOutput(
            status="succeeded", run_id="search-run", validate_only=False,
            dry_run=False, requested_source_ids=["zhihu_search"],
            results=[CrawlValidationResult(
                source_id="zhihu_search", run_id="search-run", status="succeeded",
                validate_only=False, dry_run=False, observation_id="search-observation",
                observed_at=END, normalized_items=[{
                    "content_hash": "search-content", "title": "Search",
                    "platform": "zhihu", "source_origin": "official_api",
                    "signal_role": "search_enrichment_signal", "fetched_at": END.isoformat(),
                    "raw_metrics": {"vote_count": 12},
                }],
            )],
        )
        record_collection_round(
            db, collection=collection, tracked_events=[event],
            event_ids_by_content_hash={"search-content": "event-1"},
        )
        analysis = assemble_event_heat_analysis_from_db(
            db, event=event, run_id="analysis-run", window_end=END,
            configs={"zhihu": CONFIG, "weibo": CONFIG}, persist=False,
        )
        zhihu = analysis.platforms["zhihu"]
        assert zhihu.latest_platform_heat is not None
        assert zhihu.continuous_topn_minutes is None
        assert zhihu.trend_status == "unknown"
        assert "no_topn_evidence" in zhihu.quality_flags


def test_display_requires_same_run_and_explicit_primary_platform() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        event = Event(event_id="event-1", title="Card")
        db.add(event)
        db.commit()
        event.event_detail_json = {"classification_detail": {
            "run_id": "analysis-run", "window_start": START.isoformat(),
            "window_end": END.isoformat(),
        }}
        record_platform_observations(db, event=event, observations=[
            observation("zhihu", 15, rank=10, score=0.4),
            observation("zhihu", 16, rank=5, score=0.6),
            observation("weibo", 15, score=0.6, metrics={
                "matched_status_count": 2, "weibo_search_total_number_proxy": 100,
            }),
            observation("weibo", 16, score=0.3, metrics={
                "matched_status_count": 3, "weibo_search_total_number_proxy": 80,
            }),
        ])
        analysis = assemble_event_heat_analysis_from_db(
            db, event=event, run_id="analysis-run",
            configs={"zhihu": CONFIG, "weibo": CONFIG},
        )
        assert analysis.platforms["zhihu"].trend_status == "rising"
        assert analysis.platforms["weibo"].trend_status == "cooling"
        base = {
            "event_id": "event-1", "title": "Card", "summary": "Summary",
            "evidence_summary": EvidenceSummary(lead="Evidence"),
            "source_citations": [{
                "title": "Source", "source_name": "Source", "source_type": "community_hotlist",
                "source_status": "use", "source_origin": "rsshub", "platform": "weibo",
                "fetched_at": END.isoformat(), "quality_flags": ["missing_url"],
            }],
            "classification_detail": event.event_detail_json["classification_detail"],
            "platform_heat_analysis": analysis,
        }
        zhihu = EventCard(**base, primary_platform="zhihu")
        assert zhihu.trend_status == "rising" and zhihu.rank_delta == 5
        weibo = EventCard(**base, primary_platform="weibo")
        assert weibo.trend_status == "cooling" and weibo.rank_delta is None
        unspecified = EventCard(**base)
        assert unspecified.trend_status == "unknown"
        stale = EventCard(**{
            **base, "classification_detail": {
                **base["classification_detail"], "run_id": "another-run",
            },
        }, primary_platform="zhihu")
        assert stale.platform_heat_analysis is None and stale.trend_status == "unknown"


def test_day46_db_rejects_old_rescored_items_without_observation_metadata() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        event = Event(event_id="event-1", title="Legacy")
        db.add(event)
        db.flush()
        db.add(PlatformScore(
            event_id=event.id, platform="zhihu", normalized_score=0.9,
            calculated_at=END,
            score_detail_json={"platform_presence": {"zhihu_topn": True}},
        ))
        db.commit()
        result = assemble_hotspot_classification_from_db(
            run_id="day46", event=event, db=db, window_end=END, persist=False,
        )
        assert result.classification.platform_presence.zhihu_topn is False
        heat = assemble_event_heat_analysis_from_db(
            db, event=event, run_id="day46", window_end=END,
            configs={"zhihu": CONFIG, "weibo": CONFIG}, persist=False,
        )
        assert "missing_observation_metadata" in heat.platforms["zhihu"].quality_flags


def test_day47_tool_is_registered_and_can_analyze_without_persisting_dry_run() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        event = Event(event_id="event-1", title="Tool")
        db.add(event)
        db.commit()
        assert build_default_tool_registry().get("analyze_event_heat").name == "analyze_event_heat"
        output = analyze_event_heat_handler(
            AnalyzeEventHeatInput(
                run_id="analysis-run", event_ids=["event-1"], window_end=END,
                configs={"zhihu": CONFIG, "weibo": CONFIG}, dry_run=True,
            ),
            ToolContext(db_session=db),
        )
        assert output.status == "succeeded"
        assert output.analyses[0].status == "unknown"
        assert event.event_detail_json is None


def test_init_db_adds_day47_columns_and_unique_indexes_to_existing_sqlite() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE platform_scores (id INTEGER PRIMARY KEY, event_id INTEGER, "
            "platform VARCHAR(80))"
        )
        connection.exec_driver_sql(
            "CREATE TABLE event_snapshots (id INTEGER PRIMARY KEY, event_id INTEGER, "
            "snapshot_at DATETIME)"
        )
    init_db(engine)
    inspector = inspect(engine)
    assert "observation_id" in {
        column["name"] for column in inspector.get_columns("platform_scores")
    }
    assert {"ux_platform_score_observation", "ux_event_snapshot_at"} <= {
        index["name"]
        for table in ("platform_scores", "event_snapshots")
        for index in inspector.get_indexes(table)
    }
