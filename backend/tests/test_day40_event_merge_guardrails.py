from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.init_db import init_db
from app.guardrails import default_guardrail_registry
from app.models import AgentTask, GuardrailViolation
from app.schemas import EventSignal, RetrievedEventMatchCandidate
from app.tools import ToolContext, default_tool_registry


def signal(
    *,
    event_signal_id: str = "sig-1",
    source_signal_ids: list[str] | None = None,
    title: str = "Alpha recalls product",
    keywords: list[str] | None = None,
    entities: list[str] | None = None,
    action_terms: list[str] | None = None,
    event_time_hint: str | None = "2026-09-14T10:00:00+08:00",
    is_weak_signal: bool = False,
) -> EventSignal:
    return EventSignal(
        event_signal_id=event_signal_id,
        source_signal_ids=source_signal_ids or ["source-sig-1"],
        title=title,
        keywords=keywords if keywords is not None else ["alpha", "recall", "product"],
        entities=entities if entities is not None else ["Alpha"],
        action_terms=action_terms if action_terms is not None else ["recall"],
        event_type="consumer",
        event_time_hint=event_time_hint,
        event_text_for_match=f"{title} alpha recall product",
        semantic_fingerprint={"event_text_for_embedding": f"{title} alpha recall product"},
        confidence=0.9,
        is_weak_signal=is_weak_signal,
        weak_signal_reason="weak topic seed" if is_weak_signal else None,
        source_statuses=["use"],
        source_roles=["event_signal"],
        platforms=["zhihu"],
    )


def candidate(
    *,
    event_id: str = "evt-existing",
    title: str = "Alpha product recall update",
    keywords: list[str] | None = None,
    entities: list[str] | None = None,
    action_terms: list[str] | None = None,
    event_time_hint: str | None = "2026-09-14T11:00:00+08:00",
    source_signal_ids: list[str] | None = None,
    source_urls: list[str] | None = None,
) -> RetrievedEventMatchCandidate:
    return RetrievedEventMatchCandidate(
        event_id=event_id,
        title=title,
        keywords=keywords if keywords is not None else ["alpha", "recall", "product"],
        entities=entities if entities is not None else ["Alpha"],
        action_terms=action_terms if action_terms is not None else ["recall"],
        event_type="consumer",
        event_time_hint=event_time_hint,
        event_text_for_match=f"{title} alpha recall product",
        event_text_for_embedding=f"{title} alpha recall product",
        source_signal_ids=source_signal_ids or [],
        source_urls=source_urls or [],
    )


def resolve(
    event_signal: EventSignal,
    existing_events: list[RetrievedEventMatchCandidate],
    *,
    extra: dict | None = None,
    db=None,
    context: ToolContext | None = None,
):
    payload = {
        "run_id": "run-day40",
        "event_signals": [event_signal.model_dump(mode="json")],
        "existing_events": [item.model_dump(mode="json") for item in existing_events],
    }
    if extra:
        payload.update(extra)
    return default_tool_registry.call(
        "match_and_resolve_events",
        payload,
        db=db,
        context=context,
    )


def test_day40_event_merge_rules_are_registered() -> None:
    names = {rule.name for rule in default_guardrail_registry.list_rules()}

    assert {
        "event_merge.low_confidence",
        "event_merge.entity_conflict",
        "event_merge.time_conflict",
        "event_merge.embedding_conflict",
        "event_merge.embedding_only",
        "event_merge.weak_signal_only",
    }.issubset(names)


def test_entity_conflict_blocks_and_persists_full_violation_context() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    session = sessionmaker(bind=engine)()
    task = AgentTask(task_type="event_resolver", status="running", title="day40")
    session.add(task)
    session.commit()

    result = resolve(
        signal(event_signal_id="sig-conflict", entities=["Alpha"]),
        [candidate(event_id="evt-beta", entities=["Beta"])],
        db=session,
        context=ToolContext(task_id=task.id),
    )

    resolution = result.output["event_resolutions"][0]
    rows = session.scalars(
        select(GuardrailViolation).order_by(GuardrailViolation.id)
    ).all()

    assert resolution["action"] == "reject"
    assert resolution["guardrail_status"] == "block"
    assert any(row.rule_name == "event_merge.entity_conflict" for row in rows)
    entity_row = next(row for row in rows if row.rule_name == "event_merge.entity_conflict")
    assert entity_row.event_signal_id == "sig-conflict"
    assert entity_row.event_id == "evt-beta"
    assert entity_row.agent_task_id == task.id
    assert entity_row.tool_call_id is not None
    assert entity_row.confidence == 0.0
    assert "entity_conflict" in entity_row.guardrail_flags_json
    assert entity_row.source_signal_ids_json == ["source-sig-1"]
    assert "entity_overlap" in entity_row.match_features_json


def test_time_conflict_blocks_merge() -> None:
    result = resolve(
        signal(event_time_hint="2026-09-14T10:00:00+08:00"),
        [candidate(event_id="evt-old", event_time_hint="2026-01-01T10:00:00+08:00")],
    )

    resolution = result.output["event_resolutions"][0]
    assert resolution["action"] == "reject"
    assert "time_conflict" in resolution["match_features_json"]["guardrail_flags"]


def test_embedding_conflict_blocks_and_marks_semantic_false_positive_risk() -> None:
    result = resolve(
        signal(
            title="Same viral topic",
            entities=["Alpha"],
            action_terms=[],
            event_time_hint=None,
        ),
        [
            candidate(
                event_id="evt-embedding-conflict",
                title="Same viral topic",
                entities=["Beta"],
                action_terms=[],
                event_time_hint=None,
            )
        ],
    )

    flags = result.output["event_resolutions"][0]["match_features_json"]["guardrail_flags"]
    assert result.output["event_resolutions"][0]["action"] == "reject"
    assert "embedding_conflict" in flags
    assert "semantic_false_positive_risk" in flags


def test_embedding_only_match_requires_candidate_review() -> None:
    result = resolve(
        signal(
            title="Generic viral topic",
            entities=[],
            action_terms=[],
            event_time_hint=None,
            is_weak_signal=True,
        ),
        [
            candidate(
                event_id="evt-embedding-only",
                title="Generic viral topic",
                entities=[],
                action_terms=[],
                event_time_hint=None,
            )
        ],
    )

    resolution = result.output["event_resolutions"][0]
    assert resolution["action"] == "candidate_review"
    assert resolution["guardrail_status"] == "warn"
    assert resolution["candidate_review"]["review_type"] == "event_merge_candidate"
    assert "embedding_only_without_hard_constraint" in resolution["candidate_review"][
        "guardrail_flags"
    ]


def test_weak_signal_without_hard_support_warns_for_review() -> None:
    result = resolve(
        signal(
            title="Short hot topic",
            keywords=["topic"],
            entities=[],
            action_terms=[],
            event_time_hint=None,
            is_weak_signal=True,
        ),
        [],
    )

    resolution = result.output["event_resolutions"][0]
    assert resolution["action"] == "create"
    assert resolution["guardrail_status"] == "warn"
    assert resolution["review_required"] is True
    assert "weak_signal_only" in resolution["match_features_json"]["guardrail_flags"]


def test_url_hard_match_passes_even_for_weak_signal() -> None:
    result = resolve(
        signal(
            source_signal_ids=["source-with-url"],
            entities=[],
            action_terms=[],
            is_weak_signal=True,
        ),
        [
            candidate(
                event_id="evt-url",
                entities=[],
                action_terms=[],
                source_urls=["https://example.test/news/1"],
            )
        ],
        extra={
            "source_refs_by_signal_id": {
                "source-with-url": {
                    "source_signal_id": "source-with-url",
                    "url": "https://example.test/news/1/",
                }
            }
        },
    )

    resolution = result.output["event_resolutions"][0]
    assert resolution["action"] == "merge"
    assert resolution["guardrail_status"] == "pass"
    assert resolution["event_id"] == "evt-url"


def test_different_urls_do_not_block_entity_time_candidate_review() -> None:
    result = resolve(
        signal(source_signal_ids=["source-url-a"]),
        [candidate(event_id="evt-same", source_urls=["https://example.test/b"])],
        extra={
            "source_refs_by_signal_id": {
                "source-url-a": {
                    "source_signal_id": "source-url-a",
                    "url": "https://example.test/a",
                }
            },
            "match_config": {"auto_merge_confidence_threshold": 0.99},
        },
    )

    resolution = result.output["event_resolutions"][0]
    assert resolution["action"] == "candidate_review"
    assert "url_match" not in resolution["matched_by"]
    assert "entity_time_rule" in resolution["matched_by"]
