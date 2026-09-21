from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.init_db import init_db
from app.models import Event, Item, PlatformScore, Source
from app.schemas import PlatformHeatSignalInput
from app.services.platform_heat import (
    aggregate_platform_scores,
    item_to_platform_heat_signal,
    score_weibo_platform,
    score_zhihu_platform,
    weighted_available,
)
from app.tools import default_tool_registry

NOW = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)


def test_item_adapter_maps_platform_heat_signal_fields() -> None:
    item = _item(
        source=_source(platform="zhihu", source_origin="official_api"),
        event_id=1,
        rank=4,
        raw_metrics_json={"rank": 4, "vote_count": 300, "comment_count": 20},
        normalized_json={
            "snapshot_presence_count": 2,
            "rank_delta_direction": "rising",
        },
    )

    signal = item_to_platform_heat_signal(item, event_id="evt-zhihu")

    assert signal.event_id == "evt-zhihu"
    assert signal.platform == "zhihu"
    assert signal.source_origin == "official_api"
    assert signal.signal_role == "attention_signal"
    assert signal.rank == 4
    assert signal.vote_count == 300
    assert signal.comment_count == 20
    assert signal.snapshot_presence_count == 2
    assert signal.rank_delta_direction == "rising"


def test_zhihu_rank_only_outputs_partial_and_insufficient_history() -> None:
    score = score_zhihu_platform(
        PlatformHeatSignalInput(
            event_id="evt-zhihu",
            platform="zhihu",
            source_origin="official_api",
            signal_role="attention_signal",
            rank=2,
        )
    )

    assert score.signal_score is not None
    assert score.score_status == "partial"
    assert score.platform_bucket == "top3"
    assert "insufficient_history" in score.quality_flags


def test_zhihu_rank_snapshot_and_engagement_outputs_ok() -> None:
    score = score_zhihu_platform(
        PlatformHeatSignalInput(
            event_id="evt-zhihu",
            platform="zhihu",
            source_origin="official_api",
            signal_role="attention_signal",
            rank=8,
            vote_count=1200,
            comment_count=80,
            snapshot_presence_count=3,
            rank_delta_direction="rising",
        )
    )

    assert score.score_status == "ok"
    assert score.signal_score is not None
    assert score.raw_metrics_used["vote_count"] == 1200


def test_zhihu_search_only_does_not_mark_topn() -> None:
    aggregated = aggregate_platform_scores(
        [
            score_zhihu_platform(
                PlatformHeatSignalInput(
                    event_id="evt-zhihu",
                    platform="zhihu",
                    source_origin="official_api",
                    signal_role="search_enrichment_signal",
                    vote_count=500,
                    comment_count=50,
                    search_enrichment_only=True,
                )
            )
        ]
    )[0]

    assert aggregated.platform_presence.zhihu_search is True
    assert aggregated.platform_presence.zhihu_topn is False
    assert aggregated.score_status == "partial"


def test_weibo_rsshub_only_outputs_partial() -> None:
    score = score_weibo_platform(
        PlatformHeatSignalInput(
            event_id="evt-weibo",
            platform="weibo",
            source_origin="rsshub",
            signal_role="topic_discovery_signal",
            list_position=5,
            topic_present=True,
        )
    )

    assert score.score_status == "partial"
    assert score.primary_platform_rank == 5
    assert score.platform_bucket == "top10"


def test_weibo_rsshub_plus_cli_outputs_ok() -> None:
    score = score_weibo_platform(
        PlatformHeatSignalInput(
            event_id="evt-weibo",
            platform="weibo",
            source_origin="rsshub",
            signal_role="topic_discovery_signal",
            list_position=5,
            topic_present=True,
            weibo_search_total_number_proxy=30000,
            matched_status_count=20,
            top_status_repost_count=200,
            top_status_comment_count=100,
            top_status_like_count=500,
            latest_status_created_at=NOW.isoformat(),
        )
    )

    assert score.score_status == "ok"
    assert score.signal_score is not None
    assert "weibo_search_total_number_proxy" in score.raw_metrics_used


def test_weibo_cli_only_does_not_mark_topn() -> None:
    aggregated = aggregate_platform_scores(
        [
            score_weibo_platform(
                PlatformHeatSignalInput(
                    event_id="evt-weibo",
                    platform="weibo",
                    source_origin="weibo_cli",
                    signal_role="search_enrichment_signal",
                    weibo_search_total_number_proxy=1000,
                    matched_status_count=5,
                    top_status_like_count=300,
                )
            )
        ]
    )[0]

    assert aggregated.platform_presence.weibo_cli is True
    assert aggregated.platform_presence.weibo_topn is False
    assert aggregated.score_status == "partial"


def test_weibo_low_relevance_cli_does_not_participate() -> None:
    score = score_weibo_platform(
        PlatformHeatSignalInput(
            event_id="evt-weibo",
            platform="weibo",
            source_origin="weibo_cli",
            signal_role="search_enrichment_signal",
            weibo_search_total_number_proxy=1000,
            cli_relevance_ok=False,
        )
    )

    assert score.signal_score is None
    assert score.score_status == "unknown"
    assert "weibo_cli_low_relevance" in score.quality_flags


def test_missing_fields_are_not_treated_as_zero_and_weights_are_renormalized() -> None:
    assert weighted_available([(0.8, 0.5), (None, 0.5)]) == 0.8
    assert weighted_available([(None, 0.5), (None, 0.5)]) is None


def test_multi_signal_aggregation_uses_max_plus_capped_bonus() -> None:
    scores = aggregate_platform_scores(
        [
            score_zhihu_platform(
                PlatformHeatSignalInput(
                    event_id="evt-zhihu",
                    platform="zhihu",
                    source_origin="official_api",
                    signal_role="attention_signal",
                    rank=10,
                )
            ),
            score_zhihu_platform(
                PlatformHeatSignalInput(
                    event_id="evt-zhihu",
                    platform="zhihu",
                    source_origin="official_api",
                    signal_role="attention_signal",
                    rank=2,
                )
            ),
        ]
    )

    best_signal = max(score.signal_score or 0 for score in scores[0].signal_scores)
    assert scores[0].platform_score == min(1.0, best_signal + 0.05)


def test_calculate_event_scores_writes_platform_score_using_stable_event_id_lookup() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    init_db(engine)
    session = sessionmaker(bind=engine)()
    source = _source(platform="zhihu", source_origin="official_api")
    event = Event(
        event_id="evt-zhihu",
        title="知乎热榜事件",
    )
    session.add_all([source, event])
    session.commit()
    session.refresh(source)
    session.refresh(event)
    session.add(
        _item(
            source=source,
            event_id=event.id,
            rank=3,
            raw_metrics_json={"rank": 3, "vote_count": 1000, "comment_count": 50},
            normalized_json={"snapshot_presence_count": 2},
        )
    )
    session.commit()

    result = default_tool_registry.call(
        "calculate_event_scores",
        {"event_ids": ["evt-zhihu"], "platforms": ["zhihu"]},
        db=session,
    )

    records = session.scalars(select(PlatformScore)).all()
    assert result.status == "succeeded"
    assert result.output["saved_count"] == 1
    assert records[0].event_id == event.id
    assert records[0].score_detail_json["business_event_id"] == "evt-zhihu"
    assert "platform_score_id" not in records[0].score_detail_json


def _source(*, platform: str, source_origin: str) -> Source:
    return Source(
        name=f"{platform}-{source_origin}-source",
        source_type="community_hotlist",
        source_status="use",
        source_origin=source_origin,
        platform=platform,
    )


def _item(
    *,
    source: Source,
    event_id: int,
    rank: int | None = None,
    source_origin: str | None = None,
    signal_role: str = "attention_signal",
    raw_metrics_json: dict | None = None,
    normalized_json: dict | None = None,
    quality_flags_json: list[str] | None = None,
) -> Item:
    return Item(
        source=source,
        event_id=event_id,
        title="平台热度测试",
        url="https://example.test/item",
        source_status="use",
        source_origin=source_origin or source.source_origin,
        signal_role=signal_role,
        content_hash=f"hash-{source.platform}-{rank}-{signal_role}",
        fetched_at=NOW,
        raw_metrics_json=raw_metrics_json or {},
        normalized_json=normalized_json or {},
        quality_flags_json=quality_flags_json or [],
    )
