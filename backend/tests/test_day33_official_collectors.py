import httpx

import app.tools.default_tools as default_tools
from app.collectors import CollectorRegistry, OfficialRSSCollector, OfficialRSSSourceConfig
from app.tools import ToolContext, ToolDefinition, ToolRegistry

CHINANEWS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>中国新闻网事件</title>
      <link>https://www.chinanews.com.cn/cj/2026/09-10/10693964.shtml</link>
      <description><![CDATA[<p>中国新闻网摘要</p>]]></description>
      <pubDate>Thu, 10 Sep 2026 15:00:03 +0800</pubDate>
      <guid>chinanews-1</guid>
    </item>
  </channel>
</rss>
"""

PEOPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>人民网事件</title>
      <link>http://politics.people.com.cn/n1/2025/0605/c1001-40494898.html</link>
      <description>人民网摘要</description>
      <pubDate>Thu, 05 Jun 2025 08:00:00 +0800</pubDate>
      <author>人民网</author>
      <guid>people-1</guid>
    </item>
  </channel>
</rss>
"""

XINHUA_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>新华网事件</title>
      <link>http://www.news.cn/politics/2022-12/14/c_1129207254.htm</link>
      <description>新华网摘要</description>
      <author>www.xinhuanet.com</author>
      <guid>xinhua-1</guid>
    </item>
  </channel>
</rss>
"""


def test_official_rss_collector_normalizes_chinanews_missing_author() -> None:
    collector = _collector(
        source_id="chinanews_scroll_rss",
        source_name="中国新闻网即时新闻 RSS",
        platform="chinanews",
        status="use",
        rss=CHINANEWS_RSS,
    )

    result = collector.collect(_run_config("chinanews_scroll_rss", limit=1))

    assert result.status == "succeeded"
    assert result.source_type == "official_news"
    assert result.source_origin == "official_rss"
    assert result.signal_role == "evidence_signal"
    item = result.normalized_items[0]
    assert item["title"] == "中国新闻网事件"
    assert item["summary"] == "中国新闻网摘要"
    assert item["raw_metrics"]["source_authority_weight"] == 0.8
    assert "missing_author" in item["quality_flags"]
    assert "html_summary" in item["quality_flags"]
    assert "no_raw_metrics" in item["quality_flags"]
    assert "hot_value" not in item["raw_metrics"]
    assert item["normalized"]["metric_semantics"] == "official_evidence_not_public_attention"


def test_official_rss_collector_marks_people_stale_feed_candidate() -> None:
    collector = _collector(
        source_id="people_politics_rss",
        source_name="人民网时政 RSS",
        platform="people",
        status="use",
        rss=PEOPLE_RSS,
        default_author="人民网",
    )

    result = collector.collect(_run_config("people_politics_rss", limit=1))

    assert result.status == "succeeded"
    item = result.normalized_items[0]
    assert item["platform"] == "people"
    assert item["author"] == "人民网"
    assert "stale_feed_candidate" in item["quality_flags"]


def test_official_rss_collector_allows_xinhua_missing_pubdate_as_fallback() -> None:
    collector = _collector(
        source_id="xinhua_politics_rss",
        source_name="新华网时政 RSS",
        platform="xinhua",
        status="fallback",
        rss=XINHUA_RSS,
    )

    result = collector.collect(_run_config("xinhua_politics_rss", limit=1))

    assert result.status == "succeeded"
    assert result.source_status == "fallback"
    item = result.normalized_items[0]
    assert item["published_at"] is None
    assert "missing_published_at" in item["quality_flags"]
    assert "fallback_source" in item["quality_flags"]


def test_fetch_source_items_tool_dispatches_official_collectors(monkeypatch) -> None:
    collector_registry = CollectorRegistry()
    collector_registry.register(
        _collector(
            source_id="chinanews_scroll_rss",
            source_name="中国新闻网即时新闻 RSS",
            platform="chinanews",
            status="use",
            rss=CHINANEWS_RSS,
        )
    )
    collector_registry.register(
        _collector(
            source_id="people_politics_rss",
            source_name="人民网时政 RSS",
            platform="people",
            status="use",
            rss=PEOPLE_RSS,
        )
    )
    collector_registry.register(
        _collector(
            source_id="xinhua_politics_rss",
            source_name="新华网时政 RSS",
            platform="xinhua",
            status="fallback",
            rss=XINHUA_RSS,
        )
    )
    monkeypatch.setattr(default_tools, "default_collector_registry", collector_registry)

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="fetch_source_items",
            description="test official rss fetch",
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
            "source_ids": [
                "chinanews_scroll_rss",
                "people_politics_rss",
                "xinhua_politics_rss",
            ],
            "limit": 1,
            "validate_only": True,
        },
        context=ToolContext(actor="collector_agent"),
    )

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["status"] == "succeeded"
    assert len(result.output["items"]) == 3
    assert {item["source_type"] for item in result.output["items"]} == {"official_news"}


def _collector(
    *,
    source_id: str,
    source_name: str,
    platform: str,
    status: str,
    rss: str,
    default_author: str | None = None,
) -> OfficialRSSCollector:
    return OfficialRSSCollector(
        OfficialRSSSourceConfig(
            source_id=source_id,
            source_name=source_name,
            platform=platform,
            rss_url=f"https://{platform}.example/rss.xml",
            channel="测试频道",
            source_status=status,
            authority_weight=0.8,
            default_author=default_author,
        ),
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=rss.encode("utf-8"))
            )
        ),
    )


def _run_config(source_id: str, *, limit: int):
    from app.schemas import CollectorRunConfig

    return CollectorRunConfig(
        source_id=source_id,
        limit=limit,
        validate_only=True,
        dry_run=True,
    )
