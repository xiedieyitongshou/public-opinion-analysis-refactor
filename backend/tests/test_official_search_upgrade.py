"""Actual HTTP adapters with local transport, plus query-to-persistence-to-support checks."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.init_db import init_db
from app.models import Event, Item
from app.schemas.official_support import OfficialSupportConfig
from app.services.official_search import (
    OfficialSearchClient,
    enrich_event_support,
    parse_chinanews,
    publication_time,
)

TITLE = "国家医保局通报医保支付改革试点进展"


def response_record(**overrides):
    return {
        "title": TITLE,
        "url": "https://www.chinanews.com.cn/cj/test.shtml",
        "pubtime": "2026-09-29 10:00:00",
        "content_without_tag": TITLE,
        **overrides,
    }


def test_chinanews_parses_json_without_executing_javascript():
    records = [response_record(title="医保 <em>改革</em>")]
    client = OfficialSearchClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, text="var docArr = " + json.dumps(records) + ";"
                )
            )
        )
    )
    result = client.search("医保改革", source="chinanews")
    assert result.status == "succeeded"
    assert result.items[0].title == "医保 改革"
    assert result.items[0].source_origin == "official_search"
    assert result.items[0].normalized["official_query"] == "医保改革"
    with pytest.raises(ValueError):
        parse_chinanews("var docArr = stealCredentials();")


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ("var docArr = [];", "succeeded"),
        ("<html>please retry</html>", "failed"),
        ('var docArr = {"unexpected": 1};', "failed"),
    ],
)
def test_empty_and_changed_response_are_distinct(body, status):
    client = OfficialSearchClient(
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
    )
    assert client.search("医保", source="chinanews").status == status


def test_people_post_envelope_and_failure_semantics():
    def transport(request):
        assert request.method == "POST"
        assert json.loads(request.content)["key"] == "医保"
        return httpx.Response(200, json={"code": "0", "data": {"records": []}})

    result = OfficialSearchClient(httpx.Client(transport=httpx.MockTransport(transport))).search(
        "医保", source="people"
    )
    assert result.status == "succeeded" and not result.items
    failed = OfficialSearchClient(
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(405)))
    ).search("医保", source="people")
    assert failed.status == "failed" and failed.http_status == 405


def test_search_candidate_rejects_non_official_hostname():
    body = (
        "var docArr = "
        + json.dumps([response_record(url="https://www.chinanews.com.cn.evil.test/story")])
        + ";"
    )
    result = OfficialSearchClient(
        httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
    ).search("医保", source="chinanews")
    assert result.status == "failed"
    assert not result.items


def test_publication_time_uses_china_offset_not_utc_for_naive_site_time():
    assert publication_time("2026-09-29 10:00:00").isoformat() == "2026-09-29T02:00:00+00:00"
    assert publication_time("not a time") is None
    assert publication_time(1727560800000) == publication_time(1727560800)


def test_query_results_persist_then_match_and_retry_uses_cache():
    calls = []

    def transport(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(405)
        records = [
            response_record(),
            response_record(
                title="高校赴安哥拉开展讲座",
                content_without_tag="国际教育交流",
                url="https://www.chinanews.com.cn/edu/other.shtml",
            ),
        ]
        return httpx.Response(200, text="var docArr = " + json.dumps(records) + ";")

    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    try:
        with Session(engine) as db:
            event = Event(
                event_id="target",
                title=TITLE,
                keywords_json=["医保支付改革"],
                first_seen_at=datetime(2026, 9, 29, tzinfo=UTC),
                last_seen_at=datetime(2026, 9, 29, tzinfo=UTC),
                event_detail_json={
                    "match_features": {"entities": ["国家医保局"], "action_terms": ["通报"]}
                },
            )
            db.add(event)
            db.commit()
            client = OfficialSearchClient(httpx.Client(transport=httpx.MockTransport(transport)))
            for _ in range(2):
                result = enrich_event_support(
                    db,
                    event,
                    [],
                    run_id="run",
                    budget=[1],
                    config=OfficialSupportConfig(),
                    client=client,
                )
                assert result.official_support_status == "supported"
                assert len(result.official_references) == 1
                assert result.official_references[0].item_id is not None
                assert "official_query_incomplete" in result.quality_flags
            assert len(calls) == 2
            assert db.scalar(select(func.count()).select_from(Item)) == 2
            assert event.event_detail_json["official_search"]["cache_hit"]
    finally:
        engine.dispose()


def test_all_searches_failed_is_not_checked_and_budget_does_not_call_network():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    try:
        with Session(engine) as db:
            event = Event(event_id="target", title=TITLE)
            db.add(event)
            db.commit()
            calls = []

            def transport(request):
                calls.append(request)
                return httpx.Response(503)

            client = OfficialSearchClient(httpx.Client(transport=httpx.MockTransport(transport)))
            result = enrich_event_support(
                db,
                event,
                [],
                run_id="zero",
                budget=[0],
                config=OfficialSupportConfig(),
                client=client,
            )
            assert "official_query_budget_exhausted" in result.quality_flags
            assert not calls
            result = enrich_event_support(
                db,
                event,
                [],
                run_id="failed",
                budget=[1],
                config=OfficialSupportConfig(),
                client=client,
            )
            assert result.official_support_status == "not_checked"
            assert len(calls) == 2
    finally:
        engine.dispose()
