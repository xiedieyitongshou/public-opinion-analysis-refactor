"""The two reasoning modes share separate F records and rule-only evidence links."""

from collections import Counter

import pytest
from sqlalchemy import select
from test_hotspot_analysis import (
    START,
    TITLE,
    register_official_feed,
    request,
)
from test_hotspot_analysis import environment as environment

from app.agents.analysis import HotspotAnalysisAgent
from app.models import Event, HumanReviewTask
from app.services.llm_client import reasoning_scope


@pytest.mark.parametrize("mode", ["rules", "llm"])
@pytest.mark.parametrize(
    "title", ["冷热酸甜一碰就倒牙？别把小酸痛拖成大牙病", "11月起这些新规将影响你我生活"]
)
def test_official_news_never_needs_llm_or_weak_signal_review(
    environment,
    monkeypatch,
    mode,
    title,
):
    import app.services.llm_client as llm
    import app.services.llm_event_workflow as workflow

    db, client, _ = environment
    client.titles = [title]
    monkeypatch.setattr("test_hotspot_analysis.TITLE", title)
    register_official_feed(environment, monkeypatch, published_at=START)
    monkeypatch.setattr(llm, "require_llm_configuration", lambda: None)
    monkeypatch.setattr(workflow, "get_llm_client", lambda: pytest.fail("Official news called LLM"))
    data = request("official-" + mode, START, sources=["chinanews_scroll_rss"])
    result = HotspotAnalysisAgent().run(db, data.model_copy(update={"reasoning_mode": mode}))
    assert result.status in {"succeeded", "partial"}, result.errors
    assert len(result.event_ids) == 1
    assert result.classifications[0].classification.priority_category == "F_official_only"
    assert not list(db.scalars(select(HumanReviewTask)))
    stages = {t["tool"] for t in result.tasks}
    assert "build_official_agenda" in stages
    assert len(result.classifications[0].classification_detail_json["source_signals"]) == 1


@pytest.mark.parametrize("mode", ["rules", "llm"])
def test_same_article_supports_community_but_keeps_independent_f(environment, monkeypatch, mode):
    import app.services.llm_client as llm
    import app.services.llm_event_workflow as workflow
    from app.agents.event_extractor import EventSignalAssembler, RuleEventExtractor
    from app.agents.event_matcher import EventMatcher
    from app.schemas import EventMatchConfig, ExtractEventSignalsOutput

    db, client, _ = environment
    seen = []

    def extract(data):
        assert all(s.platform in {"zhihu", "weibo"} for s in data.source_signals)
        seen.extend(s.source_signal_id for s in data.source_signals)
        return ExtractEventSignalsOutput(
            run_id=data.run_id,
            status="succeeded",
            event_signals=[
                EventSignalAssembler().assemble(
                    run_id=data.run_id, result=RuleEventExtractor().extract(s)
                )
                for s in data.source_signals
            ],
        )

    def resolve(signal, candidates, refs):
        assert set(signal.platforms) <= {"zhihu", "weibo"}
        assert not any(c.event_id.startswith("official-") for c in candidates)
        with reasoning_scope("rules"):
            return EventMatcher().match_event(
                signal=signal,
                candidates=candidates,
                config=EventMatchConfig(),
                source_refs_by_signal_id={r.source_signal_id: r for r in refs},
            )

    monkeypatch.setattr(llm, "require_llm_configuration", lambda: None)
    monkeypatch.setattr(workflow, "extract_signals", extract)
    monkeypatch.setattr(workflow, "resolve_signal", resolve)
    monkeypatch.setattr(workflow, "match_official", lambda *a, **k: pytest.fail("LLM evidence"))
    client.titles = [TITLE]
    register_official_feed(environment, monkeypatch, published_at=START)
    agent = HotspotAnalysisAgent()
    data = request(
        "separate", START, sources=["zhihu_hot_list", "chinanews_scroll_rss"]
    ).model_copy(update={"reasoning_mode": mode})
    result = agent.run(db, data)
    assert len(result.event_ids) == 2, result.errors
    assert Counter(c.classification.priority_category for c in result.classifications) == {
        "D_single_platform_with_official": 1,
        "F_official_only": 1,
    }
    for event in db.scalars(select(Event)):
        official = [i.source.source_type == "official_news" for i in event.items]
        assert all(official) or not any(official)
    replay = agent.run(db, data)
    assert replay.event_ids == result.event_ids
    assert len(list(db.scalars(select(Event)))) == 2
    assert bool(seen) == (mode == "llm")

    from app.models import AgentTask
    from app.models.briefing import AnalysisRun
    from app.services.briefing import generate_briefing, review_briefing

    collected = db.scalar(select(AgentTask).where(AgentTask.task_type == "fetch_source_items"))
    db.add(
        AnalysisRun(
            run_id=data.run_id,
            status=result.status,
            started_at=START,
            window_end=START,
            input_json=data.model_dump(mode="json"),
            output_json=result.model_dump(mode="json"),
            collection_json=collected.output_json,
        )
    )
    db.commit()
    brief = generate_briefing(db, data.run_id)
    official_cards = next(s for s in brief.sections if s.key == "official").event_cards
    assert len(official_cards) == 1
    assert official_cards[0].priority_category == "F_official_only"
    assert not review_briefing(brief)["errors"]


def test_official_evidence_is_rule_only_under_llm_scope(environment, monkeypatch):
    import app.services.llm_event_workflow as workflow
    from app.schemas.official_support import OfficialSupportConfig
    from app.services.official_support import match_official_support

    db, client, _ = environment
    client.titles = [TITLE]
    register_official_feed(environment, monkeypatch, published_at=START)
    result = HotspotAnalysisAgent().run(
        db, request("evidence", START, sources=["zhihu_hot_list", "chinanews_scroll_rss"])
    )
    events = list(db.scalars(select(Event)))
    community = next(e for e in events if any(i.source.platform == "zhihu" for i in e.items))
    official = next(e for e in events if e is not community)
    monkeypatch.setattr(workflow, "match_official", lambda *a, **k: pytest.fail("LLM evidence"))
    with reasoning_scope("llm"):
        support = match_official_support(
            community,
            official.items,
            OfficialSupportConfig(),
            window_end=START,
            allow_prior_reports=True,
        )
    assert support.official_support_status == "supported"
    assert result.classifications


def test_query_rss_candidates_are_bounded_and_irrelevant_rows_drop():
    from types import SimpleNamespace

    from app.schemas.official_paths import OfficialQuery
    from app.services.official_search import search_rss_pool

    rows = [
        SimpleNamespace(
            id=i,
            title="某汽车品牌发布召回通知",
            summary="召回车辆",
            url=f"https://news.test/{i}",
            source_id=1,
        )
        for i in range(20)
    ]
    rows.append(
        SimpleNamespace(
            id=21,
            title="乒乓球比赛夺冠",
            summary="决赛比分",
            url="https://news.test/21",
            source_id=2,
        )
    )
    selected, audit = search_rss_pool(rows, OfficialQuery(query_text="汽车召回"), limit=3)
    assert len(selected) == 3
    assert all(row.id != 21 for row in selected)
    assert audit["provider"] == "saved_rss"


@pytest.mark.parametrize("reverse", [False, True])
def test_manual_review_cannot_cross_official_and_community_domains(
    environment, monkeypatch, reverse
):
    from types import SimpleNamespace

    from app.services.human_review import _apply_decision_side_effect
    from app.services.official_agenda import is_official_agenda_event

    db, client, _ = environment
    client.titles = [TITLE]
    register_official_feed(environment, monkeypatch, published_at=START)
    HotspotAnalysisAgent().run(
        db, request("manual-domain", START, sources=["zhihu_hot_list", "chinanews_scroll_rss"])
    )
    events = list(db.scalars(select(Event)))
    official = next(e for e in events if is_official_agenda_event(e))
    community = next(e for e in events if not is_official_agenda_event(e))
    target, incoming = (official, community) if reverse else (community, official)
    item = incoming.items[0]
    with pytest.raises(ValueError, match="不能跨链路归并"):
        _apply_decision_side_effect(
            db,
            {"candidate_event_id": target.event_id, "item_ids": [item.id]},
            SimpleNamespace(decision="merge_to_existing"),
            now=START,
        )
    assert item.event_id == incoming.id
