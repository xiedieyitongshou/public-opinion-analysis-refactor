from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.init_db import init_db
from app.db.session import get_db
from app.guardrails import default_guardrail_registry
from app.main import app
from app.models import AgentTask, AgentToolCall, GuardrailViolation, Source
from app.schemas import GuardrailCheckInput
from app.tools import default_tool_registry


def publishable_card(**overrides: object) -> dict[str, object]:
    card: dict[str, object] = {
        "event_id": "event-1",
        "title": "社区事件",
        "summary": "微博出现相关讨论。",
        "priority_category": "E_single_platform_only",
        "official_support_status": "not_found",
        "platform_presence": {
            "weibo_topn": True,
            "zhihu_topn": False,
            "official_source": False,
        },
        "evidence_summary": {
            "lead": "微博热搜出现该候选。",
            "conservative_language_required": True,
        },
        "source_citations": [
            {
                "title": "微博热搜",
                "url": "https://m.weibo.cn/search?q=event",
                "source_name": "微博 RSSHub",
                "source_type": "community_hotlist",
                "source_status": "use",
                "source_origin": "rsshub",
                "platform": "weibo",
                "fetched_at": "2026-09-11T10:00:00+08:00",
                "quality_flags": [],
            }
        ],
    }
    card.update(overrides)
    return card


def test_default_guardrail_registry_declares_day26_rules() -> None:
    names = {rule.name for rule in default_guardrail_registry.list_rules()}

    assert {
        "briefing.no_source_citation",
        "source.missing_url",
        "classification.community_without_official_requires_conservative",
        "classification.official_only_not_public_heat",
        "source.audit_only_search_not_supporting",
        "summary.single_community_source_fact_claim",
    }.issubset(names)


def test_guardrails_pass_for_conservative_sourced_community_card() -> None:
    result = default_guardrail_registry.run(
        GuardrailCheckInput(payload={"event_cards": [publishable_card()]})
    )

    assert result.status == "pass"
    assert result.blocks_publish is False


def test_guardrails_block_event_without_source_citation() -> None:
    card = publishable_card(source_citations=[])
    result = default_guardrail_registry.run(GuardrailCheckInput(payload={"event_cards": [card]}))

    assert result.status == "block"
    assert result.blocks_publish is True
    assert any(v.rule_name == "briefing.no_source_citation" for v in result.violations)


def test_guardrails_warn_when_no_official_support_without_conservative_language() -> None:
    card = publishable_card(
        evidence_summary={
            "lead": "微博热搜出现该候选。",
            "conservative_language_required": False,
        }
    )
    result = default_guardrail_registry.run(GuardrailCheckInput(payload={"event_cards": [card]}))

    assert result.status == "warn"
    assert result.review_required is True


def test_guardrails_persist_violations() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    session = sessionmaker(bind=engine)()

    default_guardrail_registry.run(
        GuardrailCheckInput(
            payload={"event_cards": [publishable_card(source_citations=[])]},
            subject_type="daily_briefing",
            subject_id="briefing-1",
        ),
        db=session,
    )

    rows = session.scalars(select(GuardrailViolation)).all()
    assert len(rows) >= 1
    assert rows[0].rule_name == "briefing.no_source_citation"
    assert rows[0].severity == "block"


def test_run_briefing_guardrails_tool_uses_real_registry() -> None:
    result = default_tool_registry.call(
        "run_briefing_guardrails",
        {"payload": {"event_cards": [publishable_card(source_citations=[])]}},
    )

    assert result.status == "succeeded"
    assert result.output is not None
    assert result.output["status"] == "block"
    assert result.output["blocks_publish"] is True


def test_ops_api_exposes_guardrails_and_runtime_lists() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    session.add(
        Source(
            name="知乎热榜",
            source_type="community_hotlist",
            source_status="use",
            source_origin="official_api",
            platform="zhihu",
            is_active=True,
        )
    )
    session.add(AgentTask(task_type="daily_briefing", status="pending", title="test"))
    session.add(AgentToolCall(tool_name="fetch_source_items", status="succeeded"))
    session.commit()
    session.close()

    def override_db() -> Generator[Session, None, None]:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        rules = client.get("/ops/guardrails/rules")
        sources = client.get("/ops/sources")
        tasks = client.get("/ops/agent-tasks")
        calls = client.get("/ops/tool-calls")
    finally:
        app.dependency_overrides.clear()

    assert rules.status_code == 200
    assert rules.json()["count"] >= 1
    assert sources.json()["items"][0]["source_origin"] == "official_api"
    assert tasks.json()["count"] == 1
    assert calls.json()["count"] == 1
