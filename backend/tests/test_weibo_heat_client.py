import json

import httpx

from app.schemas import NormalizedItem
from app.services.weibo_heat_client import (
    CLIProcessResult,
    WeiboHeatClient,
    WeiboHeatClientConfig,
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
                            "created_at": "Wed Sep 09 10:00:00 +0800 2026",
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
    assert result.items[0].raw_metrics["weibo_search_total_number_proxy"] == 88
    assert result.items[0].raw_metrics["matched_status_count"] == 1
    assert result.items[0].raw_metrics["top_status_comment_count"] == 7
    assert result.items[1].normalized["weibo_signal_status"] == "rsshub_only"


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
