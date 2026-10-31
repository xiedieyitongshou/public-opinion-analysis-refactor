from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.collectors.zhihu import ZhihuHotListCollector
from app.core.config import settings
from app.schemas import CollectorRunConfig
from app.schemas.platform_heat import PlatformHeatSignalInput
from app.services.briefing_jobs import default_analysis_input
from app.services.briefing_render import trend_label
from app.services.platform_heat import score_platform_signals
from app.services.zhihu_client import ZhihuClient, ZhihuClientConfig, parse_zhihu_heat


@pytest.mark.parametrize(("text", "value"), [
    ("3220 万热度", 32200000), ("1.2 亿热度", 120000000), ("9000热度", 9000),
    ("0 万热度", 0), (None, None), ("", None), ("正在计算", None),
    ("-2 万热度", None), ("1 万浏览", None), ("未知热度 20 万", None),
])
def test_native_display_units(text, value):
    assert parse_zhihu_heat(text) == value


def client_with(handler):
    return ZhihuClient(
        config=ZhihuClientConfig(access_secret="must-not-be-sent"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def native_row(number=2099125476149274126, heat="3220 万热度"):
    return {"detail_text": heat, "target": {
        "id": number, "title": "红果短剧", "type": "question",
        "excerpt": "短剧平台的市场变化", "answer_count": 468, "follower_count": 1035,
    }}


def config():
    return CollectorRunConfig(source_id="zhihu_hot_list", limit=20, dry_run=True)


def test_native_collector_preserves_heat_description_ids_and_provenance(monkeypatch, caplog):
    monkeypatch.setattr(settings, "zhihu_hot_list_provider", "native")

    def handler(request):
        assert request.url.host == "api.zhihu.com"
        assert "authorization" not in request.headers
        assert "x-request-timestamp" not in request.headers
        return httpx.Response(200, json={"data": [native_row()]})

    with caplog.at_level("INFO", logger="app"):
        result = ZhihuHotListCollector(client_with(handler)).collect(config())
    assert result.status == "succeeded"
    assert result.list_complete
    item = result.normalized_items[0]
    assert item["summary"] == "短剧平台的市场变化"
    assert item["url"].endswith("/2099125476149274126")
    assert item["raw_metrics"]["hot_value"] == 32200000
    assert item["raw_metrics"]["answer_count"] == 468
    assert item["raw_payload"]["NativePayload"]["detail_text"] == "3220 万热度"
    assert '"hot_list_provider": "native"' in result.sampling_signature
    assert "heat_available=1" in caplog.text


def test_native_failure_no_paid_fallback_or_retry(monkeypatch):
    monkeypatch.setattr(settings, "zhihu_hot_list_provider", "native")
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(403, json={"error": {"code": 40362}})

    result = ZhihuHotListCollector(client_with(handler)).collect(config())
    assert result.status == "failed"
    assert result.list_complete is False
    assert len(calls) == 1
    assert "must-not-be-sent" not in result.error_message


def test_malformed_native_response_never_establishes_absence(monkeypatch):
    monkeypatch.setattr(settings, "zhihu_hot_list_provider", "native")
    client = client_with(lambda request: httpx.Response(200, json={"data": [None]}))
    result = ZhihuHotListCollector(client).collect(config())
    assert not result.list_complete
    assert not result.normalized_items


def signal(heat=None, *, votes=0, rank=1, provider="zhihu_native_hotlist"):
    return PlatformHeatSignalInput(
        event_id="evt", platform="zhihu", source_origin="official_api",
        signal_role="attention_signal", rank=rank, native_heat=heat, vote_count=votes,
        raw_metrics={"heat_provider": provider, "heat_text": "3220 万热度"},
    )


def test_native_heat_only_no_engagement_merge_or_history_bonus():
    first = signal(32200000, votes=0)
    second = signal(32200000, votes=1000000, rank=20)
    second.snapshot_presence_count = 100
    score = score_platform_signals([first])[0]
    assert score.platform_score == pytest.approx(32200000 / 42200000)
    assert score_platform_signals([second])[0].platform_score == score.platform_score
    aggregate = score_platform_signals([first, second, signal(10000000)])[0]
    assert aggregate.platform_score == score.platform_score
    assert aggregate.raw_metrics_used["hot_value"] == 32200000
    assert "vote_count" not in aggregate.raw_metrics_used


def test_missing_heat_rank_fallback_and_real_zero_are_distinct():
    missing = score_platform_signals([signal()])[0]
    zero = score_platform_signals([signal(0)])[0]
    assert missing.score_status == "partial"
    assert "hot_value" not in missing.raw_metrics_used
    assert "rank_only_fallback" in missing.quality_flags
    assert zero.score_status == "ok"
    assert zero.platform_score == 0
    assert zero.raw_metrics_used["hot_value"] == 0


def test_missing_native_heat_does_not_pick_historical_search_score():
    missing = signal(rank=20)
    search = signal(votes=1000000, provider="legacy")
    search.search_enrichment_only = True
    search.signal_role = "search_enrichment_signal"
    single = score_platform_signals([missing])[0]
    combined = score_platform_signals([missing, search])[0]
    assert combined.platform_score == single.platform_score
    assert "vote_count" not in combined.raw_metrics_used


def test_empty_native_board_is_unavailable_instead_of_all_topics_offlist(monkeypatch):
    monkeypatch.setattr(settings, "zhihu_hot_list_provider", "native")
    client = client_with(lambda request: httpx.Response(200, json={"data": []}))
    result = ZhihuHotListCollector(client).collect(config())
    assert result.status == "failed"
    assert not result.list_complete


def test_search_archived_default_and_explicit_legacy_switch(monkeypatch):
    monkeypatch.setattr(settings, "zhihu_search_enrichment_enabled", False)
    data = default_analysis_input("native-run")
    assert "zhihu_search" not in data.collection.source_ids
    assert "zhihu_search" not in data.collection.per_source_params
    monkeypatch.setattr(settings, "zhihu_search_enrichment_enabled", True)
    assert "zhihu_search" in default_analysis_input("legacy-run").collection.source_ids


def native_observations():
    from test_day47_platform_trend import observation

    before = observation("zhihu", 13, rank=1, score=0.5,
                         metrics={"hot_value": 10000000}, config_version="native-v1")
    after = observation("zhihu", 14, rank=5, score=0.7,
                        metrics={"hot_value": 20000000}, config_version="native-v1")
    return before, after


def test_native_trend_uses_heat_even_when_rank_falls():
    from test_day47_platform_trend import analyze

    result = analyze("zhihu", list(native_observations()))
    assert result.trend_basis == "native_heat"
    assert result.trend_status == "rising"
    assert result.native_heat_change_ratio == 1
    assert result.rank_delta == -4


def test_native_trend_does_not_compare_against_old_formula():
    from test_day47_platform_trend import analyze

    before, after = native_observations()
    before.score_config_version = "old-formula"
    result = analyze("zhihu", [before, after])
    assert result.trend_status == "unknown"
    assert "incomparable_scores" in result.quality_flags


def test_native_briefing_renders_original_heat_instead_of_system_score():
    from test_day47_platform_trend import analyze

    before, after = native_observations()
    after.platform_heat.raw_metrics_used["heat_text"] = "2000 万热度"
    trend = analyze("zhihu", [before, after], end=after.observed_at + timedelta(minutes=5))
    card = SimpleNamespace(platform_heat_analysis=SimpleNamespace(platforms={"zhihu": trend}))
    label = trend_label(card, "zhihu")
    assert "2000 万热度" in label
    assert "+100.0%" in label
    assert "系统计算" not in label


def test_native_main_observation_excludes_search_even_when_legacy_enabled(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from test_zhihu_heat_enrichment import NOW, SEED, answer, collection

    from app.db.init_db import init_db
    from app.models import Event
    from app.schemas.platform_trend import PlatformTrendConfig
    from app.services.platform_trend_assembly import (
        assemble_event_heat_analysis_from_db,
        record_collection_round,
    )

    monkeypatch.setattr(settings, "zhihu_search_enrichment_enabled", True)
    data = collection([answer(1, votes=1000000)])
    data.results[0].normalized_items = [{
        **SEED, "raw_metrics": {"rank": 5, "hot_value": 32200000,
                               "heat_provider": "zhihu_native_hotlist"},
    }]
    engine = create_engine("sqlite://")
    init_db(engine)
    with Session(engine) as db:
        event = Event(event_id="evt", title="question")
        db.add(event)
        db.commit()
        record_collection_round(db, collection=data, tracked_events=[event],
                                event_ids_by_content_hash={"seed": "evt"})
        result = assemble_event_heat_analysis_from_db(
            db, event=event, run_id="run", window_end=NOW,
            configs={"zhihu": PlatformTrendConfig(expected_interval_minutes=60)}, persist=False,
        )
        heat = result.platforms["zhihu"].latest_platform_heat
        assert heat.raw_metrics_used["hot_value"] == 32200000
        assert "vote_count" not in heat.raw_metrics_used
        assert heat.platform_score == pytest.approx(32200000 / 42200000)
    engine.dispose()
