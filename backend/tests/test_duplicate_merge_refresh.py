"""Stored Event merges through the HTTP review entry, scoring and briefing revisions."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from test_day41_human_review_queue import client_for

from app.core.config import settings
from app.db.init_db import init_db
from app.main import app
from app.models import AgentTask, DailyReport, Event, EventSnapshot, HumanReviewTask, Item, Source
from app.models.briefing import AnalysisRun, ReportRevision
from app.models.business import PlatformScore
from app.schemas import SourceSignal
from app.schemas.analysis import HotspotAnalysisInput, HotspotAnalysisOutput
from app.schemas.collectors import FetchSourceItemsInput, FetchSourceItemsOutput
from app.schemas.display import DailyBriefing
from app.schemas.official_support import (
    OfficialReference,
    OfficialSupportDetail,
    OfficialSupportResult,
)
from app.schemas.platform_trend import PlatformTrendConfig
from app.services.briefing import as_utc, generate_briefing, save_briefing
from app.services.briefing_render import render_text
from app.services.classification_assembly import assemble_hotspot_classification_from_db
from app.services.event_duplicate_scan import event_material
from app.services.platform_trend_assembly import (
    assemble_event_heat_analysis_from_db,
    load_platform_observations,
    record_collection_round,
)
from app.services.report_window import build_window

NOW = datetime(2026, 11, 5, 8, tzinfo=UTC)
PLATFORM = {
    "zhihu": ("zhihu_hot_list", "official_api", "https://www.zhihu.com/question/"),
    "weibo": ("weibo_rsshub_hot_search", "rsshub", "https://s.weibo.com/weibo?q="),
}


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "test-admin")
    monkeypatch.setattr(settings, "matching_profile", "rules")
    monkeypatch.setattr(settings, "event_reasoning_mode", "rules")
    monkeypatch.setattr(settings, "event_extraction_mode", "rules")
    monkeypatch.setattr(settings, "weibo_cli_relevance_mode", "rules")
    monkeypatch.setattr(settings, "zhihu_search_enrichment_enabled", False)
    engine = create_engine(
        "sqlite:///" + str(tmp_path / "review.db"), connect_args={"check_same_thread": False}
    )
    init_db(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        client = client_for(factory)
        yield SimpleNamespace(db=db, factory=factory, client=client)
        client.close()
        app.dependency_overrides.clear()
    engine.dispose()


def seed(
    case,
    *,
    platforms=("zhihu", "weibo"),
    window=False,
    expired=False,
    missing_heat=False,
    official=False,
    support_source_only=False,
    weibo_cli=False,
    late_zhihu_failure=False,
):
    db = case.db
    events, items, raw_items = [], [], []
    for index, platform in enumerate(platforms):
        sid, origin, url = PLATFORM[platform]
        source = db.scalar(select(Source).where(Source.name == sid))
        if source is None:
            source = Source(name=sid, source_type="community_hotlist", platform=platform)
            db.add(source)
        event = Event(
            event_id=f"event-{index}",
            title="华澜汽车召回X1故障车辆",
            lifecycle_status="active",
            first_seen_at=NOW - timedelta(hours=2),
            last_seen_at=NOW,
            event_detail_json={"source_signal_ids": [f"s{index}"]},
        )
        db.add(event)
        db.flush()
        stamp = NOW - timedelta(hours=26) if expired and index == 0 else NOW - timedelta(hours=1)
        metrics = (
            {
                "rank": index + 2,
                "hot_value": (index + 2) * 1000000,
                "heat_provider": "zhihu_native_hotlist",
            }
            if platform == "zhihu"
            else {}
        )
        if missing_heat and platform == "zhihu":
            metrics = {"heat_provider": "zhihu_native_hotlist"}
        if weibo_cli and platform == "weibo":
            metrics = {
                "matched_status_count": 3,
                "top_status_repost_count": 600,
                "top_status_comment_count": 200,
                "top_status_like_count": 6000,
                "latest_status_created_at": stamp.isoformat(),
            }
        item = Item(
            source=source,
            event=event,
            title=event.title,
            url=url + str(1000 + index),
            source_origin=origin,
            signal_role="topic_discovery_signal",
            content_hash=f"content-{index}",
            fetched_at=stamp,
            summary="华澜汽车发布X1同一批次车辆召回通知，车主讨论故障原因。",
            raw_metrics_json=metrics,
        )
        db.add(item)
        db.flush()
        signal = SourceSignal(
            source_signal_id=f"s{index}",
            item_id=str(item.id),
            source_id=sid,
            source_type="community_hotlist",
            source_status="use",
            source_origin=origin,
            platform=platform,
            signal_role="topic_discovery_signal",
            title=item.title,
            url=item.url,
            fetched_at=stamp,
            raw_metrics=metrics,
        )
        normalized = {
            "platform": platform,
            "topic_present": True,
            "source_signal": signal.model_dump(mode="json"),
        }
        item.normalized_json = normalized
        item.source_citation_json = {
            "item_id": str(item.id),
            "title": item.title,
            "url": item.url,
            "source_name": sid,
        }
        raw = {
            "item_id": str(item.id),
            "source_id": sid,
            "source_type": "community_hotlist",
            "source_status": "use",
            "source_origin": origin,
            "platform": platform,
            "signal_role": "topic_discovery_signal",
            "title": item.title,
            "url": item.url,
            "content_hash": item.content_hash,
            "fetched_at": stamp.isoformat(),
            "raw_metrics": metrics,
            "normalized": normalized,
        }
        events.append(event)
        items.append(item)
        raw_items.append(raw)
    db.commit()
    rounds = []
    for number, end in enumerate([NOW - timedelta(hours=1), NOW]):
        results = []
        for platform in dict.fromkeys(platforms):
            sid, origin, _ = PLATFORM[platform]
            stamp = (
                NOW - timedelta(hours=26 - number) if expired and platform == platforms[0] else end
            )
            hits = [deepcopy(v) for v in raw_items if v["platform"] == platform]
            for raw in hits:
                raw["fetched_at"] = stamp.isoformat()
                if weibo_cli and platform == "weibo" and number == 1:
                    raw["raw_metrics"].update(
                        top_status_repost_count=900,
                        top_status_comment_count=400,
                        top_status_like_count=12000,
                    )
            failed = late_zhihu_failure and platform == "zhihu" and number == 1
            if failed:
                hits = []
            results.append(
                {
                    "source_id": sid,
                    "run_id": f"round-{number}",
                    "source_name": sid,
                    "source_status": "use",
                    "source_type": "community_hotlist",
                    "source_origin": origin,
                    "platform": platform,
                    "signal_role": "topic_discovery_signal",
                    "status": "failed" if failed else "succeeded",
                    "validate_only": False,
                    "dry_run": False,
                    "normalized_items": hits,
                    "returned_count": len(hits),
                    "observation_id": f"round-{number}:{sid}",
                    "observed_at": stamp,
                    "list_complete": not failed,
                    "topn_scope": "top20",
                    "sampling_signature": f"stable-{platform}",
                }
            )
        collected = FetchSourceItemsOutput(
            status="succeeded",
            run_id=f"round-{number}",
            validate_only=False,
            dry_run=False,
            requested_source_ids=[v["source_id"] for v in results],
            results=results,
        )
        data = HotspotAnalysisInput(
            run_id=collected.run_id,
            window_end=end,
            collection=FetchSourceItemsInput(validate_only=False, dry_run=False),
            reasoning_mode="rules",
            matching_profile="rules",
        )
        data.trend_configs = {
            p: PlatformTrendConfig(expected_interval_minutes=60) for p in ("zhihu", "weibo")
        }
        run = AnalysisRun(
            run_id=collected.run_id,
            status="succeeded",
            started_at=end,
            window_end=end,
            input_json=data.model_dump(mode="json"),
            collection_json=collected.model_dump(mode="json"),
        )
        db.add(run)
        db.add(
            AgentTask(
                task_type="fetch_source_items",
                status="succeeded",
                input_json={"run_id": collected.run_id},
                output_json=collected.model_dump(mode="json"),
            )
        )
        db.commit()
        record_collection_round(db, collection=collected, tracked_events=events)
        if number == 0:
            first_classifications = [
                assemble_hotspot_classification_from_db(
                    db=db,
                    run_id=run.run_id,
                    event=e,
                    window_end=end,
                    source_signals=[
                        SourceSignal.model_validate(i.normalized_json["source_signal"])
                        for i in e.items
                    ],
                    official_support_result=OfficialSupportResult(
                        event_id=e.event_id,
                        official_support_status="not_found",
                    ),
                )
                for e in events
            ]
            first_analyses = [
                assemble_event_heat_analysis_from_db(
                    db,
                    event=e,
                    run_id=run.run_id,
                    window_end=end,
                    configs=data.trend_configs,
                )
                for e in events
            ]
            run.output_json = HotspotAnalysisOutput(
                run_id=run.run_id,
                plan_id="first-plan",
                status="succeeded",
                window_end=end,
                event_ids=[e.event_id for e in events],
                classifications=first_classifications,
                analyses=first_analyses,
            ).model_dump(mode="json")
            db.commit()
        rounds.append(run)
    official_item = None
    if official:
        source = Source(
            name="中国新闻网即时新闻 RSS", source_type="official_news", platform="chinanews"
        )
        official_item = Item(
            source=source,
            title=events[0].title,
            url="https://news.test/recall",
            summary="华澜汽车发布X1同一批次车辆召回通知。",
            content_hash="official",
            fetched_at=NOW,
            published_at=NOW,
            source_origin="official_rss",
            signal_role="evidence_signal",
        )
        db.add(official_item)
        db.commit()
    run = rounds[-1]
    assemblies, analyses = [], []
    for event in events:
        signals = [
            SourceSignal.model_validate(i.normalized_json["source_signal"]) for i in event.items
        ]
        supported = official and (not support_source_only or event == events[0])
        support = OfficialSupportResult(
            event_id=event.event_id,
            official_support_status="supported" if supported else "not_found",
            official_support_detail=OfficialSupportDetail(
                matched_item_ids=[str(official_item.id)] if supported else [],
            ),
            official_references=[
                OfficialReference(
                    item_id=str(official_item.id),
                    title=official_item.title,
                    url=official_item.url,
                    source_name="官媒依据",
                    source_status="use",
                    match_reason="accepted source evidence",
                )
            ]
            if supported
            else [],
        )
        assemblies.append(
            assemble_hotspot_classification_from_db(
                db=db,
                run_id=run.run_id,
                event=event,
                source_signals=signals,
                window_end=NOW,
                official_support_result=support,
            )
        )
        analyses.append(
            assemble_event_heat_analysis_from_db(
                db,
                event=event,
                run_id=run.run_id,
                window_end=NOW,
                configs=data.trend_configs,
            )
        )
    output = HotspotAnalysisOutput(
        run_id=run.run_id,
        plan_id="seed-plan",
        status="succeeded",
        window_end=NOW,
        event_ids=[e.event_id for e in events],
        classifications=assemblies,
        analyses=analyses,
    )
    run.output_json = output.model_dump(mode="json")
    db.commit()
    report = save_briefing(db, generate_briefing(db, run.run_id))
    run.report_id = report.id
    db.commit()
    if window:
        report_id = build_window(
            db,
            start=NOW - timedelta(hours=24),
            end=NOW,
            run_ids=[r.run_id for r in rounds],
            run_id="selected-window",
        )
        report = db.get(DailyReport, report_id)
    review = HumanReviewTask(
        review_type="event_duplicate_candidate",
        status="pending",
        payload_json={
            "source_event_id": events[0].event_id,
            "candidate_event_id": events[1].event_id,
            "run_ids": [r.run_id for r in rounds],
            "material_key": "isolated-test-pair",
            "event_pair_documents": [event_material(e) for e in events[:2]],
            "item_ids": [],
            "title": events[0].title,
        },
    )
    db.add(review)
    db.commit()
    return SimpleNamespace(events=events, items=items, rounds=rounds, report=report, review=review)


def confirm(case, data):
    response = case.client.post(
        f"/ops/human-review/tasks/{data.review.id}/decide",
        json={
            "decision": "merge_to_existing",
            "reviewer": "test-admin",
            "report_id": data.report.id,
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "succeeded", result
    assert result["refresh_status"] == "refreshed", result
    case.db.expire_all()
    report = case.db.get(DailyReport, result["report_id"])
    return DailyBriefing.model_validate(report.briefing_json), result


def unique_cards(briefing):
    return {c.event_id: c for s in briefing.sections for c in s.event_cards}


@pytest.mark.parametrize("window", [False, True], ids=["rolling", "selected-window"])
@pytest.mark.parametrize("weibo_cli", [False, True], ids=["rss-reference", "accepted-cli"])
def test_cross_platform_e_plus_e_reclassified_c(case, window, weibo_cli):
    data = seed(case, window=window, weibo_cli=weibo_cli)
    assert all(
        e.event_detail_json["priority_category"] == "E_single_platform_only" for e in data.events
    )
    old_json = deepcopy(data.report.briefing_json)
    original_scores = {
        s.platform: s.normalized_score
        for s in case.db.scalars(
            select(PlatformScore).where(PlatformScore.observation_id.like("round-1:%")),
        )
    }
    snapshots = {
        s.id: deepcopy(s.metrics_json)
        for s in case.db.scalars(select(EventSnapshot))
        if s.event_id == data.events[0].id
    }
    briefing, result = confirm(case, data)
    source, target = data.events
    assert source.lifecycle_status == "merged"
    assert source.event_detail_json["merged_into_event_id"] == target.event_id
    assert all(item.event_id == target.id for item in data.items)
    cards = unique_cards(briefing)
    assert briefing.event_count == 1 and set(cards) == {target.event_id}
    card = cards[target.event_id]
    assert card.priority_category == "C_cross_platform_without_official"
    assert card.cross_platform_match_type == "natural_topn_overlap"
    assert card.platform_presence.zhihu_topn and card.platform_presence.weibo_topn
    assert target.event_detail_json["priority_category"] == "C_cross_platform_without_official"
    assert target.event_detail_json["platform_presence"]["zhihu_topn"]
    assert target.event_detail_json["platform_presence"]["weibo_topn"]
    shared = next(s for s in briefing.sections if s.key == "shared")
    assert [c.event_id for c in shared.event_cards] == [target.event_id]
    assert "知乎、微博" in card.summary
    analysis = card.platform_heat_analysis
    assert analysis is not None
    assert set(analysis.platforms) == {"zhihu", "weibo"}
    assert analysis.platforms["zhihu"].latest_platform_heat.raw_metrics_used["hot_value"] == 2000000
    assert analysis.platforms["zhihu"].trend_status == "stable"
    assert analysis.platforms["weibo"].latest_platform_heat.score_basis == (
        "cli_samples" if weibo_cli else "rss_presence_only"
    )
    assert analysis.platforms["weibo"].trend_status == ("rising" if weibo_cli else "unknown")
    for platform in ("zhihu", "weibo"):
        assert (
            analysis.platforms[platform].latest_platform_heat.platform_score
            == original_scores[platform]
        )
    assert case.db.get(DailyReport, data.report.id).briefing_json == old_json
    assert case.db.get(ReportRevision, result["report_id"]).supersedes_id == data.report.id
    for sid, metrics in snapshots.items():
        assert case.db.get(EventSnapshot, sid).metrics_json == metrics


@pytest.mark.parametrize("platform", ["zhihu", "weibo"])
@pytest.mark.parametrize("window", [False, True])
def test_same_platform_scored_once_per_observation(case, platform, window):
    data = seed(case, platforms=(platform, platform), window=window)
    original_scores = [
        s.normalized_score
        for s in case.db.scalars(
            select(PlatformScore).where(PlatformScore.observation_id.like("round-1:%")),
        )
    ]
    briefing, _ = confirm(case, data)
    target = data.events[1]
    scores = list(case.db.scalars(select(PlatformScore).where(PlatformScore.event_id == target.id)))
    assert len(scores) == 2 and len({s.observation_id for s in scores}) == 2
    card = unique_cards(briefing)[target.event_id]
    assert card.priority_category == "E_single_platform_only"
    trend = card.platform_heat_analysis.platforms[platform]
    assert trend.latest_platform_heat.platform_score == max(original_scores)
    assert trend.latest_platform_heat.platform_score != pytest.approx(sum(original_scores))
    observations = load_platform_observations(
        case.db, event=target, window_start=NOW - timedelta(hours=24), window_end=NOW
    )
    assert len([o for o in observations if o.platform == platform]) == 2
    if platform == "zhihu":
        assert trend.latest_platform_heat.raw_metrics_used["hot_value"] == 3000000
    else:
        assert trend.latest_platform_heat.platform_score <= 0.20
        assert trend.latest_platform_heat.snapshot_presence_count == 2


@pytest.mark.parametrize("window", [False, True])
def test_expired_zhihu_evidence_does_not_make_current_cross_platform(case, window):
    data = seed(case, expired=True, window=window)
    briefing, _ = confirm(case, data)
    card = unique_cards(briefing)[data.events[1].event_id]
    assert card.priority_category == "E_single_platform_only"
    assert not card.platform_presence.zhihu_topn and card.platform_presence.weibo_topn
    assert "见于微博" in card.summary
    assert "知乎没有人讨论" not in render_text(briefing)


@pytest.mark.parametrize("window", [False, True])
def test_refresh_and_replay_are_idempotent_and_keep_stable_history(case, window):
    data = seed(case, window=window)
    briefing, result = confirm(case, data)
    target = data.events[1]
    before = [
        (s.id, deepcopy(s.score_detail_json), s.normalized_score)
        for s in case.db.scalars(select(PlatformScore).where(PlatformScore.event_id == target.id))
    ]
    count = case.db.scalar(select(func.count(Event.id)))
    report_count = case.db.scalar(select(func.count(DailyReport.id)))
    for _ in range(2):
        response = case.client.post(
            f"/api/reviews/{data.review.id}/refresh?report_id={result['report_id']}"
        )
        assert response.status_code == 200, response.text
        case.db.expire_all()
        assert response.json()["report_id"] == result["report_id"]
        assert case.db.scalar(select(func.count(DailyReport.id))) == report_count
    after = [
        (s.id, s.score_detail_json, s.normalized_score)
        for s in case.db.scalars(select(PlatformScore).where(PlatformScore.event_id == target.id))
    ]
    assert after == before
    assert case.db.scalar(select(func.count(Event.id))) == count == 2
    assert (
        case.db.scalar(select(func.count(Event.id)).where(Event.lifecycle_status == "active")) == 1
    )
    duplicate = case.client.post(
        f"/ops/human-review/tasks/{data.review.id}/decide",
        json={
            "decision": "merge_to_existing",
            "report_id": result["report_id"],
        },
    )
    assert duplicate.status_code == 409
    assert case.db.scalar(select(func.count(Event.id))) == 2
    observations = load_platform_observations(
        case.db, event=target, window_start=NOW - timedelta(hours=24), window_end=NOW
    )
    assert len({o.observation_id for o in observations}) == len(observations) == 4
    latest = case.db.get(DailyReport, response.json()["report_id"])
    assert (
        unique_cards(DailyBriefing.model_validate(latest.briefing_json)).keys()
        == unique_cards(briefing).keys()
    )


def test_report_identity_ignores_calculation_times_but_keeps_heat_changes(case):
    data = seed(case)
    original = deepcopy(data.report.briefing_json)
    briefing = DailyBriefing.model_validate(original)
    for section in briefing.sections:
        for card in section.event_cards:
            card.classification_detail["updated_at"] = (NOW + timedelta(days=1)).isoformat()
            card.platform_heat_analysis.updated_at = NOW + timedelta(days=1)
    identical = save_briefing(case.db, briefing)
    assert identical.id == data.report.id
    assert data.report.briefing_json == original
    for section in briefing.sections:
        for card in section.event_cards:
            heat = card.platform_heat_analysis.platforms["zhihu"].latest_platform_heat
            if heat is not None:
                heat.platform_score = 0.99
    revised = save_briefing(case.db, briefing, supersedes_id=data.report.id)
    assert revised.id != data.report.id
    assert case.db.get(ReportRevision, revised.id).supersedes_id == data.report.id
    assert data.report.briefing_json == original


@pytest.mark.parametrize("window", [False, True])
def test_platform_evidence_with_unknown_heat_still_classifies_c(case, window):
    data = seed(case, missing_heat=True, window=window)
    briefing, _ = confirm(case, data)
    card = unique_cards(briefing)[data.events[1].event_id]
    assert card.platform_presence.zhihu_topn and card.platform_presence.weibo_topn
    assert card.priority_category == "C_cross_platform_without_official"
    zhihu = card.platform_heat_analysis.platforms["zhihu"]
    assert zhihu.latest_platform_heat.platform_score is None
    assert zhihu.trend_status == "unknown"


@pytest.mark.parametrize("platform", ["zhihu", "weibo"])
@pytest.mark.parametrize("official", [False, True])
def test_d_e_descriptions_follow_actual_single_community_platform(case, platform, official):
    data = seed(case, platforms=(platform, platform), official=official)
    briefing = DailyBriefing.model_validate(data.report.briefing_json)
    expected = "D_single_platform_with_official" if official else "E_single_platform_only"
    name = "知乎" if platform == "zhihu" else "微博"
    for card in unique_cards(briefing).values():
        assert card.priority_category == expected
        assert name in card.summary
        assert card.official_support_status == ("supported" if official else "not_found")
        assert "知乎没有人讨论" not in card.summary


@pytest.mark.parametrize("window", [False, True])
def test_historical_refresh_uses_saved_observation_time_after_item_is_updated(case, window):
    data = seed(case, window=window)
    # This is the latest Item state. The original round remains saved and unchanged.
    for item in data.items:
        payload = deepcopy(item.normalized_json)
        payload["source_signal"]["fetched_at"] = (NOW + timedelta(days=1)).isoformat()
        item.fetched_at = NOW + timedelta(days=1)
        item.normalized_json = payload
    case.db.commit()
    data.review.payload_json = {
        **data.review.payload_json,
        "event_pair_documents": [event_material(e) for e in data.events],
    }
    case.db.commit()
    briefing, _ = confirm(case, data)
    assert briefing.event_count == 1
    card = unique_cards(briefing)[data.events[1].event_id]
    assert card.priority_category == "C_cross_platform_without_official"
    assert {c.platform for c in card.source_citations} == {"zhihu", "weibo"}
    assert all(as_utc(c.fetched_at) <= NOW for c in card.source_citations)


@pytest.mark.parametrize("window", [False, True])
def test_saved_collections_restore_heat_even_when_fetch_task_logs_are_missing(case, window):
    data = seed(case, window=window)
    for task in case.db.scalars(
        select(AgentTask).where(AgentTask.task_type == "fetch_source_items")
    ):
        case.db.delete(task)
    case.db.commit()
    briefing, _ = confirm(case, data)
    card = unique_cards(briefing)[data.events[1].event_id]
    assert card.priority_category == "C_cross_platform_without_official"
    heat = card.platform_heat_analysis.platforms["zhihu"].latest_platform_heat
    assert heat is not None and heat.raw_metrics_used["hot_value"] == 2000000
    observations = load_platform_observations(
        case.db, event=data.events[1], window_start=NOW - timedelta(hours=24), window_end=NOW
    )
    assert len(observations) == 4


@pytest.mark.parametrize("window", [False, True])
@pytest.mark.parametrize("platforms", [("zhihu", "weibo"), ("zhihu", "zhihu")])
def test_accepted_official_evidence_on_merged_source_survives_refresh(case, window, platforms):
    data = seed(case, platforms=platforms, window=window, official=True, support_source_only=True)
    briefing, _ = confirm(case, data)
    card = unique_cards(briefing)[data.events[1].event_id]
    category = (
        "A_cross_platform_with_official"
        if len(set(platforms)) == 2
        else "D_single_platform_with_official"
    )
    assert card.priority_category == category
    assert data.events[1].event_detail_json["priority_category"] == category
    assert card.official_support_status == "supported"
    assert any(c.source_type == "official_news" for c in card.source_citations)


def test_shorter_report_window_is_independent_from_the_current_rolling_cache(case):
    data = seed(case, late_zhihu_failure=True)
    briefing, _ = confirm(case, data)
    target = data.events[1]
    assert (
        unique_cards(briefing)[target.event_id].priority_category
        == "C_cross_platform_without_official"
    )
    current = deepcopy(target.event_detail_json)
    report_id = build_window(
        case.db,
        start=NOW - timedelta(minutes=30),
        end=NOW,
        run_ids=["round-1"],
        run_id="short-window",
    )
    short = DailyBriefing.model_validate(case.db.get(DailyReport, report_id).briefing_json)
    card = unique_cards(short)[target.event_id]
    assert card.priority_category == "E_single_platform_only"
    assert card.platform_presence.weibo_topn and not card.platform_presence.zhihu_topn
    assert target.event_detail_json == current


def test_session_summary_after_last_round_refreshes_equivalent_event_cache(case):
    data = seed(case)
    report_id = build_window(
        case.db,
        start=NOW - timedelta(hours=2),
        end=NOW + timedelta(minutes=3),
        run_ids=[r.run_id for r in data.rounds],
        run_id="session-summary",
        label="本次运行",
    )
    data.report = case.db.get(DailyReport, report_id)
    briefing, _ = confirm(case, data)
    target = data.events[1]
    assert unique_cards(briefing)[target.event_id].priority_category == (
        "C_cross_platform_without_official"
    )
    detail = target.event_detail_json
    assert detail["priority_category"] == "C_cross_platform_without_official"
    assert detail["platform_presence"]["zhihu_topn"]
    assert detail["platform_presence"]["weibo_topn"]
    assert as_utc(detail["classification_detail"]["window_end"]) == NOW + timedelta(minutes=3)
    assert as_utc(detail["platform_heat_analysis"]["window_end"]) == (
        as_utc(detail["classification_detail"]["window_end"])
    )


@pytest.mark.parametrize("unrelated_cache", [False, True])
def test_session_summary_does_not_replace_a_different_cache_scope(case, unrelated_cache):
    data = seed(case)
    target = data.events[1]
    if unrelated_cache:
        detail = deepcopy(target.event_detail_json)
        detail["classification_detail"]["run_id"] = "newer-unrelated-round"
        target.event_detail_json = detail
        case.db.commit()
    before = deepcopy(target.event_detail_json)
    report_id = build_window(
        case.db,
        start=NOW - timedelta(hours=2) if unrelated_cache else NOW - timedelta(minutes=30),
        end=NOW + timedelta(minutes=3),
        run_ids=[r.run_id for r in data.rounds],
        run_id="independent-session-summary",
        label="本次运行",
    )
    assert case.db.get(DailyReport, report_id) is not None
    assert target.event_detail_json == before


@pytest.mark.parametrize("window", [False, True])
def test_missing_all_archives_keeps_review_and_old_briefing_until_restore(case, window):
    data = seed(case, window=window)
    old_json = deepcopy(data.report.briefing_json)
    saved = {r.run_id: deepcopy(r.collection_json) for r in case.db.scalars(select(AnalysisRun))}
    for task in case.db.scalars(select(AgentTask)):
        case.db.delete(task)
    for run in case.db.scalars(select(AnalysisRun)):
        run.collection_json = None
    case.db.commit()
    response = case.client.post(
        f"/ops/human-review/tasks/{data.review.id}/decide",
        json={
            "decision": "merge_to_existing",
            "report_id": data.report.id,
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "succeeded" and result["refresh_status"] == "failed"
    case.db.expire_all()
    assert case.db.get(DailyReport, data.report.id).briefing_json == old_json
    assert case.db.scalar(select(func.count(DailyReport.id))) == (2 if window else 1)
    assert case.db.get(HumanReviewTask, data.review.id).status == "resolved"
    for key, payload in saved.items():
        case.db.get(AnalysisRun, key).collection_json = payload
    case.db.commit()
    retried = case.client.post(
        f"/api/reviews/{data.review.id}/refresh?report_id={data.report.id}",
    )
    assert retried.status_code == 200
    case.db.expire_all()
    updated = DailyBriefing.model_validate(
        case.db.get(DailyReport, retried.json()["report_id"]).briefing_json,
    )
    assert updated.event_count == 1
    assert unique_cards(updated)[data.events[1].event_id].priority_category == (
        "C_cross_platform_without_official"
    )
