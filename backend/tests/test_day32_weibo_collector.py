import json
from datetime import UTC, datetime

import httpx
import pytest

import app.tools.default_tools as default_tools
from app.collectors import CollectorRegistry, WeiboHeatCollector
from app.core.config import Settings
from app.services.request_usage import request_scope
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
    assert first_item["raw_metrics"]["weibo_search_total_number_proxy"] is None
    assert first_item["normalized"]["cli_query_total_number_proxy"] == 88
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


@pytest.mark.parametrize("limit,skip_top", [(20, 1), (20, 0), (50, 0)])
def test_default_cli_covers_entire_selected_list_within_request_budget(limit, skip_top):
    collector, queries = _full_list_collector(limit)
    with request_scope("full-weibo-list", Settings(_env_file=None).request_limits, 30) as scope:
        result = collector.collect(_run_config(limit=limit, params={"skip_top": skip_top}))

    assert result.status == "succeeded"
    assert result.list_complete is True
    assert result.normalized_count == limit - skip_top
    assert queries == [item["title"] for item in result.normalized_items]
    assert scope.counts["weibo_cli"] == limit - skip_top
    assert all(item["raw_metrics"]["matched_status_count"] == 10
               for item in result.normalized_items)
    assert all(item["normalized"]["weibo_signal_status"] == "rsshub_plus_cli"
               for item in result.normalized_items)
    sampling = json.loads(result.sampling_signature)
    assert sampling["cli_topic_limit"] == limit - skip_top
    assert sampling["cli_search_count"] == 10
    assert sampling["cli_search_sort"] == "time"


def test_cli_failure_in_middle_does_not_skip_later_topics():
    collector, queries = _full_list_collector(20, fail_at=5)
    result = collector.collect(_run_config(limit=20, params={"skip_top": 0}))

    assert result.status == "succeeded"
    assert len(queries) == result.normalized_count == 20
    assert result.normalized_items[4]["normalized"]["weibo_signal_status"] == "cli_unavailable"
    assert result.normalized_items[4]["raw_metrics"]["matched_status_count"] is None
    assert result.normalized_items[-1]["raw_metrics"]["matched_status_count"] == 10


def test_explicit_small_cli_budget_preserves_all_rss_topics_and_missing_metrics():
    collector, queries = _full_list_collector(20)
    with request_scope("small-weibo-budget", {"weibo_rsshub_hot_search": 1, "weibo_cli": 3}, 30):
        result = collector.collect(_run_config(limit=20, params={"skip_top": 0}))

    assert result.status == "succeeded"
    assert result.list_complete is True
    assert result.normalized_count == 20
    assert len(queries) == 3
    for item in result.normalized_items[3:]:
        assert "weibo_cli_request_budget_exceeded" in item["quality_flags"]
        assert item["normalized"]["weibo_signal_status"] == "cli_unavailable"
        assert item["raw_metrics"]["matched_status_count"] is None


def test_weibo_sampling_signature_records_actual_count_and_invalidates_older_sampling():
    signatures = []
    for count in (5, 10, 20):
        client = WeiboHeatClient(
            config=WeiboHeatClientConfig(
                rsshub_base_url="http://rsshub.test", cli_enabled=True, cli_search_count=count,
            ),
            http_client=httpx.Client(transport=httpx.MockTransport(_rsshub_handler)),
            cli_runner=_successful_cli_runner,
        )
        result = WeiboHeatCollector(client=client).collect(_run_config(
            limit=3,
            params={"skip_top": 1, "cli_topic_limit": 1, "cli_search_count": 999},
        ))
        assert result.status == "succeeded"
        sampling = json.loads(result.sampling_signature)
        assert sampling["cli_search_count"] == max(10, count)
        assert sampling["cli_sampling_version"] == "all-returned-v1"
        signatures.append(result.sampling_signature)
    assert signatures[0] == signatures[1]
    assert signatures[1] != signatures[2]
    old_sampling = json.loads(signatures[1])
    for key in ("cli_search_count", "cli_search_sort", "cli_sampling_version"):
        old_sampling.pop(key)
    assert json.dumps(old_sampling, sort_keys=True, ensure_ascii=False) != signatures[1]


def test_weibo_heat_collector_reports_rsshub_failure_as_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    client = _client(http_handler=handler)
    collector = WeiboHeatCollector(client=client)

    result = collector.collect(_run_config(limit=2))

    assert result.status == "failed"
    assert result.error_message is not None
    assert "RSSHub Weibo hot route unavailable" in result.error_message


@pytest.mark.parametrize("xml", [RSS_XML, "<rss><channel /></rss>"])
def test_short_or_empty_rss_is_a_complete_observable_list(xml):
    collector = WeiboHeatCollector(client=_client(
        http_handler=lambda req: httpx.Response(200, text=xml)
    ))
    result = collector.collect(_run_config(limit=20, params={"skip_top": 1}))
    assert result.status == "succeeded"
    assert result.returned_count < 19
    assert result.list_complete is True
    assert result.topn_scope == "top20:skip=1"


@pytest.mark.parametrize("xml", ["<html>upstream error</html>", "<rss />", "not XML"])
def test_invalid_rss_is_not_a_complete_empty_list(xml):
    collector = WeiboHeatCollector(client=_client(
        http_handler=lambda req: httpx.Response(200, text=xml)
    ))
    result = collector.collect(_run_config(limit=20))
    assert result.status == "failed"
    assert result.list_complete is False


def test_missing_identity_and_explicit_incompleteness_cannot_prove_absence():
    collector = WeiboHeatCollector(client=_client(
        http_handler=lambda req: httpx.Response(
            200, text="<rss><channel><item><title>残缺话题</title></item></channel></rss>"
        )
    ))
    assert collector.collect(_run_config(limit=1)).list_complete is False
    collector = WeiboHeatCollector(client=_client())
    result = collector.collect(_run_config(limit=3, params={"list_complete": False}))
    assert result.returned_count == 3
    assert result.list_complete is False


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


def _full_list_collector(limit, *, fail_at=None):
    titles = [f"城市第{index}号地铁线路正式开通" for index in range(limit)]
    xml = "<rss><channel>" + "".join(
        f"<item><title>{title}</title><link>https://m.weibo.cn/search?q={index}</link>"
        f"<guid>topic-{index}</guid></item>"
        for index, title in enumerate(titles)
    ) + "</channel></rss>"
    queries = []

    def cli_runner(command, timeout):
        assert command[command.index("--count") + 1] == "10"
        assert command[command.index("--sort") + 1] == "time"
        query = command[command.index("--q") + 1]
        queries.append(query)
        if len(queries) == fail_at:
            return _failed_cli_runner(command, timeout)
        return CLIProcessResult(returncode=0, stderr="", stdout=json.dumps({
            "statuses": [{
                "id": len(queries) * 100 + index,
                "text": f"#{query}# 最新消息",
                "created_at": datetime.now(UTC).isoformat(),
                "comments_count": index,
                "reposts_count": index,
                "attitudes_count": index,
            } for index in range(10)],
        }))

    client = WeiboHeatClient(
        config=WeiboHeatClientConfig(rsshub_base_url="http://rsshub.test", cli_enabled=True),
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, text=xml)
        )),
        cli_runner=cli_runner,
    )
    return WeiboHeatCollector(client=client), queries


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
                        "created_at": datetime.now(UTC).strftime("%a %b %d %H:%M:%S +0000 %Y"),
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
