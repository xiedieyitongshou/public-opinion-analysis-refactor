import json
from datetime import UTC, datetime

import httpx

from app.schemas import NormalizedItem
from app.services.weibo_heat_client import (
    CLIProcessResult,
    WeiboCLIEnrichment,
    WeiboHeatClient,
    WeiboHeatClientConfig,
    WeiboRSSHubItem,
    normalize_cli_status,
    normalize_heat_topic,
    parse_rsshub_items,
)

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>置顶话题</title>
      <link>https://m.weibo.cn/search?q=top</link>
      <description>置顶话题</description>
      <guid>top-guid</guid>
    </item>
    <item>
      <title>太子奶创始人李途纯去世</title>
      <link>https://m.weibo.cn/search?q=topic-1</link>
      <description>太子奶创始人李途纯去世</description>
      <guid>topic-1-guid</guid>
    </item>
    <item>
      <title>吃播圈催吐导泄都造成血钾暴跌</title>
      <link>https://m.weibo.cn/search?q=topic-2</link>
      <description>吃播圈催吐导泄都造成血钾暴跌 12345</description>
      <guid>topic-2-guid</guid>
    </item>
  </channel>
</rss>
"""


def test_parse_rsshub_items_maps_rank_and_optional_hot_value() -> None:
    items = parse_rsshub_items(RSS_XML, limit=3)

    assert len(items) == 3
    assert items[0].rank == 1
    assert items[1].title == "太子奶创始人李途纯去世"
    assert items[2].hot_value == 12345
    assert "list_position_derived_from_rss_order" in items[0].quality_flags
    assert "not_heat_metric" in items[0].quality_flags


def test_fetch_heat_uses_rsshub_and_skips_top_item() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://rsshub.test/weibo/search/hot"
        return httpx.Response(200, content=RSS_XML.encode("utf-8"))

    client = WeiboHeatClient(
        config=WeiboHeatClientConfig(
            rsshub_base_url="http://rsshub.test",
            rsshub_route="/weibo/search/hot",
            rsshub_fetch_limit=3,
            rsshub_skip_top=1,
            cli_enabled=False,
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.fetch_heat()

    assert result.rsshub["selected_count"] == 2
    assert result.items[0].topic == "太子奶创始人李途纯去世"
    assert result.items[0].normalized["weibo_signal_status"] == "rsshub_only"
    assert result.items[0].source_status == "use"
    assert result.items[0].signal_role == "topic_discovery_signal"
    assert result.items[0].signal_contribution_role == ["weak_attention_seed"]
    assert NormalizedItem.model_validate(result.items[0].model_dump()).platform == "weibo"


def test_fetch_heat_enriches_top_topics_with_weibo_cli() -> None:
    def http_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=RSS_XML.encode("utf-8"))

    def cli_runner(command: list[str], timeout_seconds: float) -> CLIProcessResult:
        assert command[:2] == ["weibo-cli", "search"]
        assert command[2] == "statuses/limited"
        assert "--type" in command
        assert command[command.index("--type") + 1] == "1"
        assert "--count" in command
        assert command[command.index("--count") + 1] == "10"
        assert timeout_seconds == 30.0
        return CLIProcessResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "total_number": 88,
                    "statuses": [
                        {
                            "id": 123,
                            "mid": "456",
                            "text": "太子奶创始人李途纯去世 相关微博",
                            "created_at": datetime.now(UTC).strftime("%a %b %d %H:%M:%S +0000 %Y"),
                            "comments_count": 7,
                            "reposts_count": 3,
                            "attitudes_count": 12,
                            "user": {"screen_name": "媒体账号"},
                        }
                    ],
                }
            ),
            stderr="",
        )

    client = WeiboHeatClient(
        config=WeiboHeatClientConfig(
            rsshub_base_url="http://rsshub.test",
            cli_enabled=True,
            cli_topic_limit=1,
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(http_handler)),
        cli_runner=cli_runner,
    )

    result = client.fetch_heat(with_cli=True)

    assert result.items[0].normalized["weibo_signal_status"] == "rsshub_plus_cli"
    assert result.items[0].raw_metrics["weibo_search_total_number_proxy"] is None
    assert result.items[0].normalized["cli_query_total_number_proxy"] == 88
    assert result.items[0].raw_metrics["matched_status_count"] == 1
    assert result.items[0].raw_metrics["top_status_comment_count"] == 7
    assert result.items[1].normalized["weibo_signal_status"] == "rsshub_only"


def test_cli_metrics_only_use_relevant_recent_statuses() -> None:
    topic = parse_rsshub_items(RSS_XML, limit=3)[1]
    statuses = [
        normalize_cli_status({
            "id": 1,
            "text": "#太子奶创始人李途纯去世# 后续消息",
            "url": "https://m.weibo.cn/status/1",
            "created_at": "2026-09-09T10:00:00+00:00",
            "comments_count": 7,
            "attitudes_count": 12,
        }),
        normalize_cli_status({
            "id": 2,
            "text": "今日抽奖送手机",
            "url": "https://m.weibo.cn/status/2",
            "created_at": "2026-09-09T10:00:00+00:00",
            "comments_count": 9999,
            "attitudes_count": 9999,
        }),
        normalize_cli_status({
            "id": 3,
            "text": "太子奶创始人李途纯去世 旧帖",
            "url": "https://m.weibo.cn/status/3",
            "created_at": "2026-09-01T10:00:00+00:00",
            "comments_count": 8888,
            "attitudes_count": 8888,
        }),
    ]
    enrichment = WeiboCLIEnrichment(
        enabled=True,
        available=True,
        command=["weibo-cli"],
        returncode=0,
        stderr=None,
        total_number_proxy=200,
        samples=statuses,
        quality_flags=[],
    )

    result = normalize_heat_topic(
        topic, fetched_at="2026-09-09T11:00:00+00:00", cli_enrichment=enrichment
    )

    assert result.normalized["cli_relevance_counts"] == {
        "returned_count": 3, "accepted_count": 1, "audit_count": 2
    }
    assert result.raw_metrics["matched_status_count"] == 1
    assert result.raw_metrics["top_status_comment_count"] == 7
    assert result.raw_metrics["top_status_like_count"] == 12
    assert result.raw_metrics["weibo_search_total_number_proxy"] is None
    assert result.normalized["cli_query_total_number_proxy"] == 200
    assert [sample.relation.decision for sample in result.cli_enrichment.samples] == [
        "accepted", "audit_only", "rejected"
    ]
    assert "weibo_cli_search_noise_detected" in result.quality_flags


def test_cli_only_unrelated_results_do_not_add_heat_metrics() -> None:
    topic = parse_rsshub_items(RSS_XML, limit=3)[1]
    result = normalize_heat_topic(
        topic,
        fetched_at="2026-09-09T11:00:00+00:00",
        cli_enrichment=WeiboCLIEnrichment(
            enabled=True,
            available=True,
            command=["weibo-cli"],
            returncode=0,
            stderr=None,
            total_number_proxy=500,
            samples=[normalize_cli_status({
                "id": 4,
                "text": "今日抽奖送手机",
                "url": "https://m.weibo.cn/status/4",
                "created_at": "2026-09-09T10:00:00+00:00",
                "comments_count": 9000,
            })],
            quality_flags=[],
        ),
    )

    assert result.normalized["weibo_signal_status"] == "rsshub_only"
    assert result.raw_metrics["matched_status_count"] == 0
    assert result.raw_metrics["top_status_comment_count"] is None
    assert result.raw_metrics["weibo_search_total_number_proxy"] is None


def test_cli_explicit_action_conflict_is_not_counted() -> None:
    topic = WeiboRSSHubItem(
        rank=1,
        title="甲大学取消周末课程",
        url="https://m.weibo.cn/search?q=school",
        description="甲大学取消周末课程",
        published_at=None,
        guid="school-topic",
        hot_value=None,
        quality_flags=[],
    )
    result = normalize_heat_topic(
        topic,
        fetched_at="2026-09-09T11:00:00+00:00",
        cli_enrichment=WeiboCLIEnrichment(
            enabled=True,
            available=True,
            command=["weibo-cli"],
            returncode=0,
            stderr=None,
            total_number_proxy=100,
            samples=[normalize_cli_status({
                "id": 5,
                "text": "甲大学增设周末课程",
                "url": "https://m.weibo.cn/status/5",
                "created_at": "2026-09-09T10:00:00+00:00",
                "attitudes_count": 9999,
            })],
            quality_flags=[],
        ),
    )

    relation = result.cli_enrichment.samples[0].relation
    assert relation.decision == "rejected"
    assert "action_conflict" in relation.rejected_by
    assert result.raw_metrics["matched_status_count"] == 0
    assert result.raw_metrics["top_status_like_count"] is None


def test_fetch_heat_marks_cli_failure_without_dropping_rsshub_topic() -> None:
    def http_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=RSS_XML.encode("utf-8"))

    def cli_runner(command: list[str], timeout_seconds: float) -> CLIProcessResult:
        return CLIProcessResult(
            returncode=1,
            stdout="",
            stderr="缺少登录令牌。请运行 `weibo-cli auth login`。",
        )

    client = WeiboHeatClient(
        config=WeiboHeatClientConfig(
            rsshub_base_url="http://rsshub.test",
            cli_enabled=True,
            cli_topic_limit=1,
        ),
        http_client=httpx.Client(transport=httpx.MockTransport(http_handler)),
        cli_runner=cli_runner,
    )

    result = client.fetch_heat(with_cli=True)

    assert result.items[0].normalized["weibo_signal_status"] == "cli_unavailable"
    assert "weibo_cli_missing_token" in result.items[0].quality_flags
    assert result.items[0].topic == "太子奶创始人李途纯去世"
