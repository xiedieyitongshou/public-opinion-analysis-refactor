import httpx
import pytest

from app.schemas import NormalizedItem
from app.services.zhihu_client import (
    ZhihuAuthError,
    ZhihuClient,
    ZhihuClientConfig,
    normalize_hot_list_items,
    normalize_search_items,
)


def test_fetch_hot_list_maps_official_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-secret"
        assert "X-Request-Timestamp" in request.headers
        assert request.url.params["Limit"] == "2"
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

    client = ZhihuClient(
        config=ZhihuClientConfig(access_secret="test-secret"),
        http_client=httpx.Client(
            base_url="https://developer.zhihu.com",
            transport=httpx.MockTransport(handler),
        ),
    )

    result = client.fetch_hot_list(limit=2)
    normalized = normalize_hot_list_items(result)

    assert result.total == 2
    assert len(result.items) == 2
    assert normalized[0]["title"] == "事件 A"
    assert normalized[0]["raw_metrics"]["rank"] == 1
    assert normalized[0]["source_origin"] == "official_api"
    assert normalized[0]["signal_role"] == "attention_signal"
    assert "community_hot_candidate" in normalized[0]["signal_contribution_role"]
    assert NormalizedItem.model_validate(normalized[0]).source_status == "use"


def test_fetch_hot_list_requires_access_secret() -> None:
    client = ZhihuClient(config=ZhihuClientConfig(access_secret=None))

    with pytest.raises(ZhihuAuthError, match="ZHIHU_ACCESS_SECRET"):
        client.fetch_hot_list(limit=1)


def test_fetch_hot_list_raises_auth_error_for_api_code() -> None:
    client = ZhihuClient(
        config=ZhihuClientConfig(access_secret="bad-secret"),
        http_client=httpx.Client(
            base_url="https://developer.zhihu.com",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"Code": 20001, "Message": "authorization failed"},
                )
            ),
        ),
    )

    with pytest.raises(ZhihuAuthError, match="authorization failed"):
        client.fetch_hot_list(limit=1)


def test_search_maps_engagement_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["Query"] == "事件 A"
        assert request.url.params["Count"] == "3"
        return httpx.Response(
            200,
            json={
                "Code": 0,
                "Data": {
                    "HasMore": False,
                    "SearchHashId": "hash-1",
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
                    ],
                },
            },
        )

    client = ZhihuClient(
        config=ZhihuClientConfig(access_secret="test-secret"),
        http_client=httpx.Client(
            base_url="https://developer.zhihu.com",
            transport=httpx.MockTransport(handler),
        ),
    )

    result = client.search(query="事件 A", count=3)
    normalized = normalize_search_items(
        result,
        candidate_title="事件 A",
        candidate_url="https://www.zhihu.com/question/1",
        candidate_rank=1,
        relation={"is_highly_related": True, "reasons": ["same_question_id"]},
    )

    assert len(result.items) == 1
    assert result.items[0].comment_count == 12
    assert result.items[0].vote_up_count == 34
    assert normalized[0]["raw_metrics"]["ranking_score"] == 0.98
    assert normalized[0]["normalized"]["candidate_rank"] == 1
    assert normalized[0]["signal_role"] == "search_enrichment_signal"
    assert NormalizedItem.model_validate(normalized[0]).source_origin == "official_api"
