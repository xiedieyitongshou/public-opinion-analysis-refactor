import json

import httpx

import app.tools.default_tools as default_tools
from app.collectors import CollectorRegistry, WeiboHeatCollector
from app.services.weibo_heat_client import CLIProcessResult, WeiboHeatClient, WeiboHeatClientConfig
from app.tools import ToolContext, ToolDefinition, ToolRegistry

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
      <title>话题一</title>
      <link>https://m.weibo.cn/search?q=topic-1</link>
      <description>话题一</description>
      <guid>topic-1-guid</guid>
    </item>
    <item>
      <title>话题二</title>
      <link>https://m.weibo.cn/search?q=topic-2</link>
      <description>话题二 12345</description>
      <guid>topic-2-guid</guid>
    </item>
  </channel>
</rss>
"""


def test_weibo_heat_collector_normalizes_rsshub_topics() -> None:
    collector = WeiboHeatCollector(client=_client())

    result = collector.collect(_run_config(limit=3, params={"skip_top": 1, "with_cli": False}))

    assert result.status == "succeeded"
    assert result.source_id == "weibo_rsshub_hot_search"
    assert result.source_origin == "rsshub"
    assert result.signal_role == "topic_discovery_signal"
    assert result.normalized_count == 2
    item = result.normalized_items[0]
    assert item["source_name"] == "微博热搜 RSSHub"
    assert item["platform"] == "weibo"
    assert item["title"] == "话题一"
    assert item["raw_metrics"]["list_position"] == 2
    assert item["normalized"]["weibo_signal_status"] == "rsshub_only"
    assert item["normalized"]["rank_semantics"] == "rss_item_order_only_not_official_rank"
    assert "not_heat_metric" in item["quality_flags"]


def test_weibo_heat_collector_enriches_selected_topics_with_cli() -> None:
    collector = WeiboHeatCollector(client=_client(cli_runner=_successful_cli_runner))

    result = collector.collect(
        _run_config(
            limit=3,
            params={"skip_top": 1, "with_cli": True, "cli_topic_limit": 1},
        )
    )

    assert result.status == "succeeded"
    assert result.normalized_count == 2
    first_item = result.normalized_items[0]
    second_item = result.normalized_items[1]
    assert first_item["normalized"]["weibo_signal_status"] == "rsshub_plus_cli"
    assert first_item["raw_metrics"]["weibo_search_total_number_proxy"] == 88
    assert first_item["raw_metrics"]["matched_status_count"] == 1
    assert first_item["raw_metrics"]["top_status_like_count"] == 12
    assert second_item["normalized"]["weibo_signal_status"] == "rsshub_only"


def test_weibo_heat_collector_keeps_rsshub_topic_when_cli_fails() -> None:
    collector = WeiboHeatCollector(client=_client(cli_runner=_failed_cli_runner))

    result = collector.collect(
        _run_config(
            limit=2,
            params={"skip_top": 1, "with_cli": True, "cli_topic_limit": 1},
        )
    )

    assert result.status == "succeeded"
    assert result.normalized_count == 1
    item = result.normalized_items[0]
    assert item["normalized"]["weibo_signal_status"] == "cli_unavailable"
    assert "weibo_cli_missing_token" in item["quality_flags"]


def test_weibo_heat_collector_reports_rsshub_failure_as_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    client = _client(http_handler=handler)
    collector = WeiboHeatCollector(client=client)

    result = collector.collect(_run_config(limit=2))

    assert result.status == "failed"
    assert result.error_message is not None
    assert "RSSHub Weibo hot route unavailable" in result.error_message


def test_fetch_source_items_tool_dispatches_weibo_collector(monkeypatch) -> None:
    collector_registry = CollectorRegistry()
    collector_registry.register(WeiboHeatCollector(client=_client()))
    monkeypatch.setattr(default_tools, "default_collector_registry", collector_registry)

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="fetch_source_items",
            description="test weibo fetch",
            input_model=default_tools.FetchSourceItemsInput,
            output_model=default_tools.FetchSourceItemsOutput,
            handler=default_tools.fetch_source_items_handler,
            has_side_effect=True,
            retryable=False,
        )
    )

    result = registry.call(
        "fetch_source_items",
        {
            "source_ids": ["weibo_rsshub_hot_search"],
            "limit": 3,
            "validate_only": True,
            "per_source_params": {
                "weibo_rsshub_hot_search": {"skip_top": 1, "with_cli": False}
            },
        },
        context=ToolContext(actor="collector_agent"),
    )

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["status"] == "succeeded"
    assert result.output["requested_source_ids"] == ["weibo_rsshub_hot_search"]
    assert len(result.output["items"]) == 2


def _client(
    *,
    http_handler=None,
    cli_runner=None,
) -> WeiboHeatClient:
    return WeiboHeatClient(
        config=WeiboHeatClientConfig(
            rsshub_base_url="http://rsshub.test",
            rsshub_route="/weibo/search/hot",
            rsshub_fetch_limit=3,
            rsshub_skip_top=0,
            cli_enabled=False,
            cli_topic_limit=0,
        ),
        http_client=httpx.Client(
            transport=httpx.MockTransport(http_handler or _rsshub_handler)
        ),
        cli_runner=cli_runner,
    )


def _run_config(*, limit: int, params: dict | None = None):
    from app.schemas import CollectorRunConfig

    return CollectorRunConfig(
        source_id="weibo_rsshub_hot_search",
        limit=limit,
        validate_only=True,
        dry_run=True,
        params=params or {},
    )


def _rsshub_handler(request: httpx.Request) -> httpx.Response:
    assert str(request.url) == "http://rsshub.test/weibo/search/hot"
    return httpx.Response(200, content=RSS_XML.encode("utf-8"))


def _successful_cli_runner(command: list[str], timeout_seconds: float) -> CLIProcessResult:
    assert command[:3] == ["weibo-cli", "search", "statuses/limited"]
    assert command[command.index("--q") + 1] == "话题一"
    return CLIProcessResult(
        returncode=0,
        stdout=json.dumps(
            {
                "total_number": 88,
                "statuses": [
                    {
                        "id": 123,
                        "mid": "456",
                        "text": "话题一 相关微博",
                        "created_at": "Wed Sep 09 10:00:00 +0800 2026",
                        "comments_count": 7,
                        "reposts_count": 3,
                        "attitudes_count": 12,
                        "user": {"screen_name": "媒体账号"},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        stderr="",
    )


def _failed_cli_runner(command: list[str], timeout_seconds: float) -> CLIProcessResult:
    return CLIProcessResult(
        returncode=1,
        stdout="",
        stderr="缺少登录令牌。请运行 `weibo-cli auth login`。",
    )
