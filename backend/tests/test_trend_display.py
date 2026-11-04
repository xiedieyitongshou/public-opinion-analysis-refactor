"""Explain missing trends without confusing old heat with the latest list check."""

from types import SimpleNamespace

import pytest
from test_day47_platform_trend import analyze, observation

from app.schemas.platform_trend import PlatformTrendConfig, PlatformTrendInput
from app.services.briefing_render import trend_label
from app.services.platform_trend import analyze_platform_trend


def label(trend):
    card = SimpleNamespace(platform_heat_analysis=SimpleNamespace(
        platforms={trend.platform: trend},
    ))
    return trend_label(card, trend.platform)


def test_offlist_distinguishes_latest_check_from_only_historical_heat():
    first = observation("zhihu", 13, rank=10, score=0.3, metrics={"heat_text": "307 万热度"})
    last = observation("zhihu", 15, present=False)
    trend = analyze("zhihu", [first, last])
    assert trend.last_collection_at == last.observed_at
    assert trend.last_observed_at == first.observed_at
    text = label(trend)
    assert "最后采集：09-26 23:00" in text
    assert "热度采于 09-26 21:00，非当前值" in text
    assert "最近在榜第 10 名" in text
    assert "趋势待确认（仅有1次在榜观测，最近一轮已离榜" in text


@pytest.mark.parametrize(("observations", "reason"), [
    ([observation("zhihu", 15, rank=10, score=0.3)], "仅有1次在榜观测"),
    ([observation("zhihu", 14, rank=10, score=0.3),
      observation("zhihu", 15, present=None, status="failed", complete=False)],
     "最近一次来源采集失败"),
    ([observation("zhihu", 14, rank=10, score=0.3),
      observation("zhihu", 15, rank=9, score=0.4, status="partial", complete=False)],
     "榜单数据不完整"),
    ([observation("zhihu", 10, rank=10, score=0.3),
      observation("zhihu", 15, rank=9, score=0.4)], "相邻采集间隔过长"),
    ([observation("zhihu", 14, rank=10, score=0.3, scope="top20"),
      observation("zhihu", 15, rank=9, score=0.4, scope="top10")],
     "前后采集的榜单范围不一致"),
    ([observation("zhihu", 10, rank=10, score=0.3)], "最近观测已过期"),
    ([], "本期没有可用的榜单采集记录"),
])
def test_unknown_labels_explain_actual_observation_constraints(observations, reason):
    trend = analyze("zhihu", observations)
    assert trend.trend_status == "unknown"
    assert reason in label(trend)
    if observations:
        assert "最后采集" in label(trend)
    else:
        assert "最后采集：未记录" in label(trend)


def test_rss_only_weibo_explains_missing_interaction_metrics():
    trend = analyze("weibo", [observation("weibo", 14, score=0.3),
                              observation("weibo", 15, score=0.4)])
    assert "趋势待确认（缺少两轮可比的CLI互动指标）" in label(trend)


def test_known_trend_keeps_its_direction_and_also_shows_collection_time():
    trend = analyze("zhihu", [observation("zhihu", 14, rank=10, score=0.3),
                              observation("zhihu", 15, rank=9, score=0.4)])
    text = label(trend)
    assert "升温 ↑" in text and "趋势待确认" not in text
    assert "最后采集：09-26 23:00" in text


def test_unknown_sampling_does_not_claim_the_latest_heat_time_is_missing():
    sample = observation("zhihu", 15, rank=10, score=0.3)
    from test_day47_platform_trend import END, START

    trend = analyze_platform_trend(PlatformTrendInput(
        event_id="event-1", platform="zhihu", run_id=sample.run_id,
        window_start=START, window_end=END, observations=[sample], config=PlatformTrendConfig(),
    ))
    assert "未配置采样间隔" in label(trend)
    assert "最后采集：09-26 23:00" in label(trend)
