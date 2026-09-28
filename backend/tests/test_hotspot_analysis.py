"""Offline acceptance checks using real collectors, tools and database writes."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.collectors.base as collector_base
import app.tools.default_tools as tool_module
from app.agents.analysis import HotspotAnalysisAgent
from app.collectors import CollectorRegistry, ZhihuHotListCollector
from app.collectors.official import OfficialRSSCollector, OfficialRSSSourceConfig
from app.db.init_db import init_db
from app.models import AgentTask, AgentToolCall, Event, EventSnapshot, HumanReviewTask, Item
from app.models.business import PlatformScore
from app.schemas.analysis import HotspotAnalysisInput
from app.schemas.collectors import FetchSourceItemsInput
from app.schemas.human_review import HumanReviewDecisionInput
from app.schemas.platform_trend import PlatformTrendConfig
from app.services.human_review import decide_human_review_task
from app.services.zhihu_client import ZhihuHotListResult

TITLE = "某品牌公司发布召回通知引发消费者关注"
OTHER_TITLE = "某大学发布招生通知引发学生关注"
START = datetime(2026, 9, 28, 4, tzinfo=UTC)


class LocalZhihuClient:
    def __init__(self):
        self.time = START
        self.titles = [OTHER_TITLE, TITLE]
        self.failed = False
        self.calls = 0

    def fetch_hot_list(self, limit):
        self.calls += 1
        if self.failed:
            raise RuntimeError("offline source outage")
        items = [
            {"Title": title, "Url": f"https://www.zhihu.com/question/{title}", "Summary": title}
            for title in self.titles[:limit]
        ]
        return ZhihuHotListResult(
            total=len(items),
            fetched_at=self.time,
            items=items,
            raw_payload={"Items": items},
        )


@pytest.fixture
def environment(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    client = LocalZhihuClient()
    collectors = CollectorRegistry()
    collectors.register(ZhihuHotListCollector(client=client))
    monkeypatch.setattr(tool_module, "default_collector_registry", collectors)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return client.time

    monkeypatch.setattr(collector_base, "datetime", Clock)
    with Session(engine) as db:
        yield db, client, collectors
    engine.dispose()


def request(run_id, time, *, sources=None, params=None):
    return HotspotAnalysisInput(
        run_id=run_id,
        window_end=time,
        collection=FetchSourceItemsInput(
            source_ids=sources or ["zhihu_hot_list"],
            limit=2,
            validate_only=False,
            dry_run=False,
            per_source_params=params or {},
        ),
        trend_configs={
            platform: PlatformTrendConfig(expected_interval_minutes=120)
            for platform in ("zhihu", "weibo")
        },
    )


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_two_rounds_keep_identity_rank_and_replay_without_duplicate_rows(environment):
    db, client, _ = environment
    agent = HotspotAnalysisAgent()
    first = agent.run(db, request("round-1", START))
    assert first.status != "failed", first.model_dump()
    assert len(first.tasks) == 9
    assert count(db, Event) == 2
    assert count(db, Item) == 2
    assert all(item.event_id is not None for item in db.scalars(select(Item)))
    assert all(
        item.source_citation_json["item_id"] == str(item.id) for item in db.scalars(select(Item))
    )
    event = db.scalar(select(Event).where(Event.title == TITLE))
    stable_id = event.event_id
    assert event.event_detail_json["classification_detail"]["run_id"] == "round-1"
    client.time += timedelta(hours=2)
    client.titles.reverse()
    second = agent.run(db, request("round-2", client.time))
    assert second.status != "failed", second.model_dump()
    trend = next(result for result in second.analyses if result.event_id == stable_id)
    assert trend.platforms["zhihu"].trend_status == "rising"
    assert trend.platforms["zhihu"].rank_delta == 1
    assert trend.platforms["zhihu"].continuous_topn_minutes == 120
    assert "classification_window_mismatch" not in trend.quality_flags
    assert "missing_classification" not in trend.quality_flags
    assert count(db, Event) == 2
    assert count(db, Item) == 2
    assert count(db, PlatformScore) == 4
    assert count(db, EventSnapshot) == 4
    replay = agent.run(db, request("round-2", client.time))
    assert replay.status != "failed", replay.model_dump()
    assert client.calls == 2
    assert count(db, EventSnapshot) == 4
    assert count(db, PlatformScore) == 4
    assert count(db, AgentTask) == count(db, AgentToolCall) == 27


def test_failed_round_updates_existing_event_to_unknown(environment):
    db, client, _ = environment
    agent = HotspotAnalysisAgent()
    first = agent.run(db, request("before-outage", START))
    assert first.event_ids
    client.time += timedelta(hours=2)
    client.failed = True
    result = agent.run(db, request("outage", client.time))
    assert result.status == "partial", result.model_dump()
    assert len(result.tasks) == 9
    assert result.tasks[0]["status"] == "failed"
    assert count(db, Event) == 2
    for analysis in result.analyses:
        trend = analysis.platforms["zhihu"]
        assert trend.current_topn_present is None
        assert trend.latest_platform_heat is None
        assert trend.trend_status == "unknown"
        assert "source_unavailable" in trend.quality_flags


def test_pending_review_keeps_item_unassociated_and_is_idempotent(environment):
    db, client, _ = environment
    client.titles = ["热议"]
    agent = HotspotAnalysisAgent()
    result = agent.run(db, request("weak", START))
    assert result.status == "partial", result.model_dump()
    assert count(db, Event) == 0
    assert count(db, HumanReviewTask) == 1
    item = db.scalar(select(Item))
    review = db.scalar(select(HumanReviewTask))
    assert item.event_id is None
    assert review.payload_json["item_ids"] == [str(item.id)]
    assert review.payload_json["source_citations"][0]["item_id"] == str(item.id)
    agent.run(db, request("weak", START))
    assert count(db, HumanReviewTask) == 1
    assert count(db, PlatformScore) == count(db, EventSnapshot) == 0
    decision = decide_human_review_task(
        db,
        review,
        HumanReviewDecisionInput(decision="create_new_event", reviewer="test"),
    )
    assert decision.status == "succeeded"
    assert item.event_id == db.scalar(select(Event)).id


@pytest.mark.parametrize("with_community", [False, True])
def test_official_items_join_event_chain_and_classification(
    environment,
    monkeypatch,
    with_community,
):
    import app.collectors.official as official_module

    db, client, collectors = environment

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return client.time

    monkeypatch.setattr(official_module, "datetime", Clock)
    xml = f"""<rss><channel><item><title>{TITLE}</title>
    <link>https://news.example.test/story</link>
    <pubDate>Mon, 28 Sep 2026 04:00:00 GMT</pubDate>
    <description>{TITLE}</description></item></channel></rss>"""
    collectors.register(
        OfficialRSSCollector(
            OfficialRSSSourceConfig(
                source_id="chinanews_scroll_rss",
                source_name="中国新闻网",
                platform="chinanews",
                rss_url="https://news.example.test/rss",
                channel="news",
                source_status="use",
                authority_weight=1.0,
            ),
            http_client=httpx.Client(
                transport=httpx.MockTransport(lambda req: httpx.Response(200, text=xml))
            ),
        )
    )
    sources = ["zhihu_hot_list"] if with_community else []
    sources.append("chinanews_scroll_rss")
    result = HotspotAnalysisAgent().run(db, request("official", START, sources=sources))
    assert result.status != "failed", result.model_dump()
    event = db.scalar(select(Event).where(Event.title == TITLE))
    classification = next(
        value for value in result.classifications if value.event_id == event.event_id
    )
    if with_community:
        assert count(db, Event) == 2
        assert len(event.items) == 2
        assert classification.classification.priority_category == "D_single_platform_with_official"
    else:
        assert count(db, Event) == 1
        assert classification.classification.priority_category == "F_official_only"
        assert "official_agenda_rank" in event.event_detail_json
        assert count(db, PlatformScore) == 0


def test_different_source_parameters_cannot_reuse_round_id(environment):
    db, _, _ = environment
    agent = HotspotAnalysisAgent()
    agent.run(db, request("same", START))
    with pytest.raises(ValueError, match="different collection parameters"):
        agent.run(db, request("same", START, sources=["weibo_rsshub_hot_search"]))
    with pytest.raises(ValueError, match="original window_end"):
        agent.run(db, request("same", START + timedelta(hours=1)))


def test_collector_and_analysis_share_existing_item_identity(environment):
    db, _, collectors = environment
    standalone = FetchSourceItemsInput(
        source_ids=["zhihu_hot_list"],
        limit=2,
        validate_only=False,
        dry_run=False,
        promote_to_event_pool=True,
    )
    collectors.collect(standalone, db=db)
    original_ids = {item.id for item in db.scalars(select(Item))}
    result = HotspotAnalysisAgent().run(db, request("adopt-items", START))
    assert result.status != "failed", result.model_dump()
    collectors.collect(standalone, db=db)
    assert {item.id for item in db.scalars(select(Item))} == original_ids
    assert all(item.event_id for item in db.scalars(select(Item)))


def test_weibo_skip_top_is_complete_and_survives_zhihu_outage(environment, monkeypatch):
    import app.services.weibo_heat_client as weibo_module
    from app.collectors.weibo import WeiboHeatCollector
    from app.services.weibo_heat_client import WeiboHeatClient, WeiboHeatClientConfig

    db, client, collectors = environment

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return client.time

    monkeypatch.setattr(weibo_module, "datetime", Clock)
    xml = f"""<rss><channel>
    <item><title>置顶</title><link>https://weibo.test/pinned</link></item>
    <item><title>{TITLE}</title><link>https://weibo.test/topic</link></item>
    </channel></rss>"""
    collectors.register(
        WeiboHeatCollector(
            client=WeiboHeatClient(
                config=WeiboHeatClientConfig(
                    rsshub_base_url="https://rsshub.test",
                    rsshub_skip_top=1,
                    cli_enabled=False,
                    cli_topic_limit=0,
                ),
                http_client=httpx.Client(
                    transport=httpx.MockTransport(lambda req: httpx.Response(200, text=xml))
                ),
            )
        )
    )
    sources = ["zhihu_hot_list", "weibo_rsshub_hot_search"]
    agent = HotspotAnalysisAgent()
    first = agent.run(db, request("cross-1", START, sources=sources))
    assert first.status != "failed", first.model_dump()
    event = db.scalar(select(Event).where(Event.title == TITLE))
    assert len(event.items) == 2
    client.time += timedelta(hours=2)
    client.failed = True
    second = agent.run(db, request("cross-2", client.time, sources=sources))
    assert second.status == "partial", second.model_dump()
    result = next(value for value in second.analyses if value.event_id == event.event_id)
    assert result.platforms["zhihu"].latest_platform_heat is None
    assert result.platforms["weibo"].current_topn_present is True
    assert result.platforms["weibo"].continuous_topn_minutes == 120
    assert result.platforms["weibo"].rank_delta is None
    assert result.platforms["weibo"].trend_status == "unknown"
    assert "incomplete_list" not in result.platforms["weibo"].quality_flags


def test_empty_collection_finishes_without_creating_events(environment):
    db, client, _ = environment
    client.titles = []
    result = HotspotAnalysisAgent().run(db, request("empty", START))
    assert result.status == "skipped", result.model_dump()
    assert len(result.tasks) == 9
    assert count(db, Event) == count(db, PlatformScore) == count(db, EventSnapshot) == 0
