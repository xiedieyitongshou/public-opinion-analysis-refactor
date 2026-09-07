import httpx
import pytest

from app.services.zhihu_client import (
    ZhihuAuthError,
    ZhihuClient,
    ZhihuClientConfig,
    normalize_hot_list_items,
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
    assert "community_heat" in normalized[0]["score_contribution_role"]


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
