import httpx

import app.tools.default_tools as default_tools
from app.collectors import CollectorRegistry, ZhihuHotListCollector, ZhihuSearchCollector
from app.services.zhihu_client import ZhihuClient, ZhihuClientConfig
from app.tools import ToolContext, ToolDefinition, ToolRegistry


def test_zhihu_hot_list_collector_normalizes_official_api_items() -> None:
    client = ZhihuClient(
        config=ZhihuClientConfig(access_secret="test-secret"),
        http_client=httpx.Client(
            base_url="https://developer.zhihu.com",
            transport=httpx.MockTransport(_zhihu_handler),
        ),
    )
    collector = ZhihuHotListCollector(client=client)

    result = collector.collect(_run_config("zhihu_hot_list", limit=2))

    assert result.status == "succeeded"
    assert result.source_id == "zhihu_hot_list"
    assert result.source_type == "community_question_hotlist"
    assert result.signal_role == "attention_signal"
    assert result.normalized_count == 2
    assert result.normalized_items[0]["source_name"] == "知乎热榜"
    assert result.normalized_items[0]["raw_metrics"]["rank"] == 1
    assert "community_hot_candidate" in result.normalized_items[0]["signal_contribution_role"]


def test_zhihu_hot_list_collector_reports_missing_secret_as_failed() -> None:
    collector = ZhihuHotListCollector(
        client=ZhihuClient(config=ZhihuClientConfig(access_secret=None))
    )

    result = collector.collect(_run_config("zhihu_hot_list", limit=1))

    assert result.status == "failed"
    assert result.error_message is not None
    assert "ZHIHU_ACCESS_SECRET" in result.error_message


def test_zhihu_search_collector_skips_without_explicit_target() -> None:
    collector = ZhihuSearchCollector(
        client=ZhihuClient(config=ZhihuClientConfig(access_secret="test-secret"))
    )

    result = collector.collect(_run_config("zhihu_search", limit=3))

    assert result.status == "skipped"
    assert "requires query" in str(result.error_message)


def test_zhihu_search_collector_enriches_candidate_with_relation() -> None:
    client = ZhihuClient(
        config=ZhihuClientConfig(access_secret="test-secret"),
        http_client=httpx.Client(
            base_url="https://developer.zhihu.com",
            transport=httpx.MockTransport(_zhihu_handler),
        ),
    )
    collector = ZhihuSearchCollector(client=client)

    result = collector.collect(
        _run_config(
            "zhihu_search",
            limit=3,
            params={
                "candidates": [
                    {
                        "title": "事件 A",
                        "url": "https://www.zhihu.com/question/1",
                        "rank": 1,
                    }
                ]
            },
        )
    )

    assert result.status == "succeeded"
    assert result.source_id == "zhihu_search"
    assert result.signal_role == "search_enrichment_signal"
    assert result.normalized_count == 1
    item = result.normalized_items[0]
    assert item["source_name"] == "知乎搜索"
    assert item["raw_metrics"]["comment_count"] == 12
    assert item["normalized"]["relation"]["decision"] == "accepted"
    assert "same_zhihu_question_id" in item["normalized"]["relation"]["matched_by"]


def test_fetch_source_items_tool_dispatches_zhihu_collectors(monkeypatch) -> None:
    client = ZhihuClient(
        config=ZhihuClientConfig(access_secret="test-secret"),
        http_client=httpx.Client(
            base_url="https://developer.zhihu.com",
            transport=httpx.MockTransport(_zhihu_handler),
        ),
    )
    collector_registry = CollectorRegistry()
    collector_registry.register(ZhihuHotListCollector(client=client))
    collector_registry.register(ZhihuSearchCollector(client=client))
    monkeypatch.setattr(default_tools, "default_collector_registry", collector_registry)

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="fetch_source_items",
            description="test zhihu fetch",
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
            "source_ids": ["zhihu_hot_list", "zhihu_search"],
            "limit": 2,
            "validate_only": True,
            "per_source_params": {
                "zhihu_search": {
                    "query": "事件 A",
                    "candidate_url": "https://www.zhihu.com/question/1",
                }
            },
        },
        context=ToolContext(actor="collector_agent"),
    )

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["status"] == "succeeded"
    assert result.output["requested_source_ids"] == ["zhihu_hot_list", "zhihu_search"]
    assert len(result.output["items"]) == 3


def _run_config(source_id: str, *, limit: int, params: dict | None = None):
    from app.schemas import CollectorRunConfig

    return CollectorRunConfig(
        source_id=source_id,
        limit=limit,
        validate_only=True,
        dry_run=True,
        params=params or {},
    )


def _zhihu_handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer test-secret"
    assert "X-Request-Timestamp" in request.headers
    if request.url.path == "/api/v1/content/hot_list":
        return httpx.Response(
            200,
            json={
                "Code": 0,
                "Data": {
                    "Total": 2,
                    "Items": [
                        {
                            "Title": "事件 A",
                            "Url": "https://www.zhihu.com/question/1",
                            "ThumbnailUrl": "https://pic.example/a.jpg",
                            "Summary": "摘要 A",
                        },
                        {
                            "Title": "事件 B",
                            "Url": "https://www.zhihu.com/question/2",
                            "Summary": "摘要 B",
                        },
                    ],
                },
            },
        )
    if request.url.path == "/api/v1/content/zhihu_search":
        assert request.url.params["Count"] in {"2", "3"}
        return httpx.Response(
            200,
            json={
                "Code": 0,
                "Data": {
                    "Items": [
                        {
                            "Title": "事件 A 最新进展",
                            "ContentType": "answer",
                            "ContentID": "100",
                            "ContentText": "事件 A 的讨论内容",
                            "Url": "https://www.zhihu.com/question/1/answer/100",
                            "CommentCount": 12,
                            "VoteUpCount": 34,
                            "AuthorName": "作者",
                            "EditTime": "2026-09-08 10:00:00",
                            "AuthorityLevel": 1,
                            "RankingScore": 0.98,
                        }
                    ]
                },
            },
        )
    return httpx.Response(404)
