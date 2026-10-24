"""Official HTML -> extractive summary -> matching, with mocked HTTP only."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.agents.event_extractor import EventSignalAssembler, RuleEventExtractor
from app.agents.normalizer import normalize_item
from app.collectors.official import OfficialRSSCollector, OfficialRSSSourceConfig
from app.core.config import settings
from app.db.init_db import init_db
from app.models import Event
from app.schemas import CollectorRunConfig, NormalizedItem
from app.services.analysis_interfaces import _candidate, item_to_source_signal
from app.services.match_documents import event_document, item_document
from app.services.official_article import enrich_official_items, extract_article, safe_url
from app.services.official_search import OfficialSearchClient, persist_search_items
from app.services.official_support import official_match_document
from app.services.request_usage import request_scope
from app.services.topic_content import summary_from_items

TITLE = "怀化国际陆港前三季度发运班列1051列"
BODY = (
    "怀化国际陆港前三季度累计发运班列1051列，共计52524标箱，同比增长16%。"
    "怀化国际陆港开通多条国际物流运输线路，促进当地企业与东盟国家的经贸往来。"
    "港口工作人员介绍，目前班列运行组织保持稳定，将继续根据企业需求优化运输服务。"
)


def html(container="left_zw", title=TITLE):
    return (
        f'<html><meta property="og:title" content="{title}">'
        '<meta property="article:published_time" content="2026-10-21T10:00:00+08:00">'
        "<nav>首页导航无关内容</nav>"
        f'<div class="{container}"><p>{BODY}</p><p>责任编辑：某某，推荐阅读其他新闻</p>'
        "<script>广告跟踪代码</script></div><footer>版权声明</footer></html>"
    )


def item(**kwargs):
    return NormalizedItem(
        source_id="chinanews_scroll_rss",
        source_name="中新网",
        source_type="official_news",
        source_origin="official_rss",
        source_status="use",
        platform="chinanews",
        signal_role="evidence_signal",
        title=TITLE,
        url="https://www.chinanews.com.cn/cj/test.shtml",
        summary="原始RSS描述",
        fetched_at=datetime.now(UTC),
        published_at=datetime(2026, 10, 21, tzinfo=UTC),
        content_hash="test-hash",
    ).model_copy(update=kwargs)


@pytest.fixture
def enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "official_article_enabled", True)
    monkeypatch.setattr(settings, "official_article_cache_dir", str(tmp_path))


@pytest.mark.parametrize(
    "container", ["left_zw", "rm_txt_con cf", "main-aticle", "article-content"]
)
def test_article_extracts_only_body(container):
    data = extract_article(html(container), TITLE)
    assert BODY in data["content"]
    assert not any(word in data["content"] for word in ["导航", "责任编辑", "广告"])
    assert data["page_published_at"].startswith("2026-10-21")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "https://www.chinanews.com.cn.evil.test/a",
        "https://user@www.chinanews.com.cn/a",
        "file:///secret",
    ],
)
def test_rejects_non_official_addresses(url):
    assert safe_url(url, "chinanews") is None


def test_full_body_summary_cache_and_matching(enabled):
    calls = []

    def transport(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=html(), headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(transport))
    original = item()
    enriched = enrich_official_items([original], client=client)[0]
    again = enrich_official_items([original], client=client)[0]
    assert len(calls) == 1 and again.normalized["official_article"]["cache_hit"]
    assert enriched.content == BODY
    assert "1051列" in enriched.summary and "16%" in enriched.summary
    assert enriched.published_at == original.published_at
    assert all(
        e["text"] in enriched.content for e in enriched.normalized["official_article"]["excerpts"]
    )
    normalized = NormalizedItem.model_validate(normalize_item(enriched.model_dump(mode="json")))
    source = item_to_source_signal(normalized, "1")
    signal = EventSignalAssembler().assemble(
        run_id="offline", result=RuleEventExtractor().extract(source)
    )
    assert "1051列" in event_document(signal).summary
    assert not signal.has_accepted_cli_context
    with Session(create_engine("sqlite:///:memory:")) as db:
        init_db(db.get_bind())
        row = persist_search_items(db, [enriched])[0]
        event = Event(event_id="test", title=TITLE, items=[row])
        db.add(event)
        db.flush()
        assert "1051列" in event_document(_candidate(event)).summary
        assert "1051列" in item_document(row).summary
        assert "1051列" in official_match_document(event).summary
        text, refs = summary_from_items([row])
        assert "1051列" in text and refs[0]["url"] == original.url


@pytest.mark.parametrize(
    "failure", ["http", "wrong_title", "no_body", "redirect", "too_large", "binary"]
)
def test_failures_keep_rss_and_are_cached(enabled, failure):
    calls = []

    def transport(request):
        calls.append(str(request.url))
        if failure == "http":
            return httpx.Response(403)
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        if failure == "too_large":
            return httpx.Response(200, text="x" * 2_000_001, headers={"content-type": "text/html"})
        if failure == "binary":
            return httpx.Response(200, content=b"png", headers={"content-type": "image/png"})
        body = html(title="完全无关的足球比赛") if failure == "wrong_title" else "<h1>登录</h1>"
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(transport))
    original = item()
    for _ in range(2):
        result = enrich_official_items([original], client=client)[0]
        assert result.summary == original.summary and result.content is None
        assert result.normalized["official_article"]["status"] == "fallback"
    assert len(calls) == 1


def test_budget_and_disabled_never_fetch(enabled, monkeypatch):
    def unexpected(request):
        raise AssertionError("network should not run")

    client = httpx.Client(transport=httpx.MockTransport(unexpected))
    with request_scope("test", {"official_article_chinanews": 0}, 30):
        result = enrich_official_items([item()], client=client)[0]
        assert result.normalized["official_article"]["reason"] == "article_request_budget"
    monkeypatch.setattr(settings, "official_article_max_fetches", 0)
    result = enrich_official_items([item()], client=client)[0]
    assert result.normalized["official_article"]["reason"] == "article_batch_limit"
    monkeypatch.setattr(settings, "official_article_enabled", False)
    assert enrich_official_items([item()], client=client)[0].normalized == {}


def test_search_fetches_article_and_rss_uses_same_path(enabled):
    def transport(request):
        if request.url.host == "sou.chinanews.com.cn":
            record = {
                "title": TITLE,
                "url": item().url,
                "content_without_tag": "搜索片段",
                "pubtime": "2026-10-21 10:00:00",
            }
            return httpx.Response(200, text="var docArr=" + json.dumps([record]) + ";")
        return httpx.Response(200, text=html(), headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(transport))
    result = OfficialSearchClient(client).search("怀化班列", source="chinanews")
    assert result.status == "succeeded" and "1051列" in result.items[0].summary
    collector = OfficialRSSCollector(
        OfficialRSSSourceConfig(
            "chinanews_scroll_rss",
            "中新网",
            "chinanews",
            "https://www.chinanews.com.cn/rss",
            "新闻",
            "use",
            0.8,
        ),
        http_client=client,
    )
    rss = collector.normalize(
        [{"title": TITLE, "link": item().url, "description": "RSS片段"}],
        CollectorRunConfig(source_id="chinanews_scroll_rss"),
    )[0]
    assert rss.normalized["official_article"]["cache_hit"]
    assert rss.summary == result.items[0].summary


def test_support_context_excludes_unaccepted_and_search_items():
    excluded = SimpleNamespace(summary="无关搜索结果", signal_role="search_enrichment_signal")
    accepted = SimpleNamespace(
        summary="港口班列发运1051列。", signal_role="evidence_signal", title=TITLE
    )
    doc = official_match_document(SimpleNamespace(title=TITLE, items=[excluded, accepted]))
    assert "1051列" in doc.summary and "无关" not in doc.summary


def test_first_empty_heading_and_editor_credits():
    page = f'<h1 class="cf"></h1><h1>{TITLE}</h1><div class="rm_txt_con"><p>{BODY}</p>'
    page += "<p>总监制丨工作人员 编辑丨某某</p></div>"
    data = extract_article(page, TITLE)
    assert data["title"] == TITLE and "总监制" not in data["content"]


def test_batch_limit_and_request_accounting(enabled, monkeypatch):
    monkeypatch.setattr(settings, "official_article_max_fetches", 1)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, text=html(), headers={"content-type": "text/html"})
        )
    )
    original = item(published_at=None)
    other = item(url="https://www.chinanews.com.cn/other.shtml")
    with request_scope("test", {"official_article_chinanews": 8}, 30) as scope:
        results = enrich_official_items([original, original, other], client=client)
    assert len(scope.records) == 1
    assert results[1].normalized["official_article"]["cache_hit"]
    assert results[2].normalized["official_article"]["reason"] == "article_batch_limit"
    assert results[0].published_at is None  # Page metadata must not silently set event time.


def test_fallback_removes_credits_without_inventing_facts(enabled, monkeypatch):
    monkeypatch.setattr(settings, "official_article_max_fetches", 0)
    original = item(summary="当地企业与东盟国家的经贸往来得到发展。一起学习↓↓↓ 总监制丨某某")
    result = enrich_official_items([original])[0]
    assert result.summary == "当地企业与东盟国家的经贸往来得到发展。"
    assert "1051" not in result.summary
