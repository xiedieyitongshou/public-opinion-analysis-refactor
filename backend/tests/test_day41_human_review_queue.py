from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.init_db import init_db
from app.db.session import get_db
from app.main import app
from app.models import Event, GuardrailViolation, HumanReviewTask
from app.tools import ToolContext, default_tool_registry


@pytest.fixture(autouse=True)
def clear_dependency_overrides() -> Generator[None, None, None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def candidate_review_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "event_signal_id": "sig-review-1",
        "candidate_event_id": "evt-existing",
        "recommended_action": "merge",
        "confidence": 0.72,
        "reason": "medium confidence match requires review",
        "matched_by": ["entity_time_rule"],
        "match_features_json": {
            "entity_overlap": 1.0,
            "action_overlap": 1.0,
            "time_distance_hours": 2.0,
            "hard_constraints_passed": True,
            "matched_by": ["entity_time_rule"],
            "guardrail_flags": ["low_confidence"],
        },
        "guardrail_flags": ["low_confidence"],
        "source_signal_ids": ["source-sig-1"],
    }
    payload.update(overrides)
    return payload


def create_review_task(session: Session, **overrides: object) -> dict:
    result = default_tool_registry.call(
        "create_human_review_task",
        {
            "run_id": "run-41",
            "candidate_review": candidate_review_payload(**overrides),
            "source_citations": [
                {
                    "title": "source title",
                    "url": "https://example.test/source",
                    "source_name": "Example Source",
                }
            ],
            "priority": 7,
        },
        context=ToolContext(task_id=None),
        db=session,
    )
    assert result.status == "succeeded"
    assert result.output is not None
    return result.output


def test_create_human_review_task_writes_candidate_review() -> None:
    session = make_session()

    output = create_review_task(session)
    rows = session.scalars(select(HumanReviewTask)).all()

    assert output["status"] == "succeeded"
    assert output["created_count"] == 1
    assert output["updated_count"] == 0
    assert len(rows) == 1
    assert rows[0].review_type == "event_merge_candidate"
    assert rows[0].status == "pending"
    assert rows[0].priority == 7
    assert rows[0].payload_json["event_signal_id"] == "sig-review-1"
    assert rows[0].payload_json["candidate_event_id"] == "evt-existing"
    assert rows[0].payload_json["run_id"] == "run-41"
    assert rows[0].payload_json["source_citations"][0]["url"] == "https://example.test/source"


def test_duplicate_candidate_review_updates_pending_task_instead_of_creating_duplicate() -> None:
    session = make_session()

    create_review_task(session)
    output = create_review_task(session)
    rows = session.scalars(select(HumanReviewTask)).all()

    assert output["created_count"] == 0
    assert output["updated_count"] == 1
    assert len(rows) == 1


def test_create_human_review_task_skips_empty_input() -> None:
    session = make_session()

    result = default_tool_registry.call("create_human_review_task", {}, db=session)

    assert result.status == "succeeded"
    assert result.output["status"] == "skipped"
    assert session.scalar(select(HumanReviewTask)) is None


def test_human_review_api_lists_pending_and_returns_detail() -> None:
    session_factory = make_session_factory()
    session = session_factory()
    created = create_review_task(session)
    task_id = created["human_review_tasks"][0]["id"]
    session.close()
    client = client_for(session_factory)

    list_response = client.get("/ops/human-review/tasks")
    detail_response = client.get(f"/ops/human-review/tasks/{task_id}")

    assert list_response.status_code == 200
    assert list_response.json()["count"] == 1
    assert list_response.json()["items"][0]["status"] == "pending"
    assert detail_response.status_code == 200
    detail = detail_response.json()["human_review_task"]
    assert detail["payload"]["event_signal_id"] == "sig-review-1"
    assert detail["payload"]["guardrail_flags"] == ["low_confidence"]


def test_merge_to_existing_records_resolved_decision_and_resolves_violation() -> None:
    session_factory = make_session_factory()
    session = session_factory()
    event = Event(
        event_id="evt-existing",
        title="Existing event",
        lifecycle_status="active",
        event_detail_json={},
    )
    violation = GuardrailViolation(
        rule_name="event_merge.low_confidence",
        rule_type="event_merge",
        severity="warn",
        message="review required",
        event_signal_id="sig-review-1",
        event_id="evt-existing",
    )
    session.add_all([event, violation])
    session.commit()
    created = create_review_task(session)
    task_id = created["human_review_tasks"][0]["id"]
    session.close()
    client = client_for(session_factory)

    response = client.post(
        f"/ops/human-review/tasks/{task_id}/decide",
        json={
            "decision": "merge_to_existing",
            "reviewer": "tester",
            "decision_reason": "same entity and time window",
        },
    )

    assert response.status_code == 200
    assert response.json()["event_id"] == "evt-existing"
    session = session_factory()
    task = session.get(HumanReviewTask, task_id)
    event = session.scalar(select(Event).where(Event.event_id == "evt-existing"))
    violation = session.scalar(select(GuardrailViolation))
    assert task.status == "resolved"
    assert task.reviewer == "tester"
    assert task.decision == "merge_to_existing"
    assert task.decision_reason == "same entity and time window"
    assert task.resolved_at is not None
    assert event.event_detail_json["human_review_decisions"][0]["event_signal_id"] == "sig-review-1"
    assert violation.resolved is True
    assert violation.resolved_at is not None


def test_create_new_event_generates_stable_business_event_id() -> None:
    session_factory = make_session_factory()
    session = session_factory()
    created = create_review_task(
        session,
        candidate_event_id=None,
        event_signal_id="sig-new-event",
    )
    task_id = created["human_review_tasks"][0]["id"]
    session.close()
    client = client_for(session_factory)

    response = client.post(
        f"/ops/human-review/tasks/{task_id}/decide",
        json={
            "decision": "create_new_event",
            "reviewer": "tester",
            "decision_reason": "no existing event matches",
            "event_title": "New reviewed event",
        },
    )

    assert response.status_code == 200
    event_id = response.json()["event_id"]
    assert event_id.startswith("evt_")
    session = session_factory()
    event = session.scalar(select(Event).where(Event.event_id == event_id))
    task = session.get(HumanReviewTask, task_id)
    assert event is not None
    assert event.title == "New reviewed event"
    assert event.event_detail_json["created_from_human_review"] is True
    assert task.status == "resolved"
    assert task.decision == "create_new_event"


def test_ignore_marks_task_ignored() -> None:
    session_factory = make_session_factory()
    session = session_factory()
    created = create_review_task(session)
    task_id = created["human_review_tasks"][0]["id"]
    session.close()
    client = client_for(session_factory)

    response = client.post(
        f"/ops/human-review/tasks/{task_id}/decide",
        json={"decision": "ignore", "reviewer": "tester", "decision_reason": "not useful"},
    )

    assert response.status_code == 200
    session = session_factory()
    task = session.get(HumanReviewTask, task_id)
    assert task.status == "ignored"
    assert task.decision == "ignore"
    assert task.resolved_at is not None


def test_reject_records_resolved_audit_without_creating_event() -> None:
    session_factory = make_session_factory()
    session = session_factory()
    created = create_review_task(session, candidate_event_id=None, event_signal_id="sig-reject")
    task_id = created["human_review_tasks"][0]["id"]
    session.close()
    client = client_for(session_factory)

    response = client.post(
        f"/ops/human-review/tasks/{task_id}/decide",
        json={"decision": "reject", "reviewer": "tester", "decision_reason": "different event"},
    )

    assert response.status_code == 200
    assert response.json()["event_id"] is None
    session = session_factory()
    assert session.scalar(select(Event)) is None
    task = session.get(HumanReviewTask, task_id)
    assert task.status == "resolved"
    assert task.decision == "reject"
    assert task.payload_json["decision_audit"]["decision"] == "reject"


def make_session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    return sessionmaker(bind=engine)


def make_session() -> Session:
    return make_session_factory()()


def client_for(session_factory) -> TestClient:
    def override_db() -> Generator[Session, None, None]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)
