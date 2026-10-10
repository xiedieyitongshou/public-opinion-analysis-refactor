"""Recipient management, selection, migration, and stable daily audiences."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_hotspot_analysis import START
from test_hotspot_analysis import environment as environment
from test_week08_briefing import configure_email, run_report
from test_week09_deployment import complete_queued, configure_daily

from app.api.email_recipients import RecipientInput, create_recipient
from app.core.config import settings
from app.db.init_db import init_db
from app.db.session import get_db
from app.main import create_app
from app.models import DailyReport
from app.models.briefing import DailyDispatch, DailyEmailAudience, EmailDelivery, EmailRecipient
from app.services.briefing import approve_report
from app.services.briefing_email import dispatch_daily, send_report
from app.services.daily_schedule import tick_daily
from app.services.email_recipients import active_recipients, initialize_recipients


@pytest.fixture
def recipient_api(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "recipient-test-admin")
    monkeypatch.setattr(settings, "email_recipients", [])
    monkeypatch.setattr(settings, "smtp_host", None)
    engine = create_engine(f"sqlite:///{(tmp_path / 'recipients.db').as_posix()}",
                           connect_args={"check_same_thread": False})
    init_db(engine)
    app = create_app()

    def session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    client = TestClient(app)  # No production lifespan / scheduler in API tests.
    client.headers["Authorization"] = "Bearer recipient-test-admin"
    yield client, engine
    client.close()
    engine.dispose()


def test_recipient_api_crud_duplicates_auth_and_restart(recipient_api, monkeypatch):
    client, engine = recipient_api
    assert client.get("/api/recipients", headers={"Authorization": ""}).status_code == 401
    response = client.post("/api/recipients", json={
        "email": " reader@Example.COM ", "note": "自己",
    })
    assert response.status_code == 201
    row = response.json()
    assert row["email"] == "reader@example.com"
    assert client.post("/api/recipients", json={"email": "READER@example.com"}).status_code == 409
    status = client.get("/api/dashboard").json()["email"]
    assert status["recipient_count"] == 1 and status["smtp_configured"] is False
    assert client.put(f"/api/recipients/{row['id']}", json={
        "email": "new@example.com", "note": "改过的备注", "enabled": False,
    }).status_code == 200
    with Session(engine) as db:
        assert db.get(EmailRecipient, row["id"]).note == "改过的备注"
        assert not active_recipients(db)
    assert client.get("/api/dashboard").json()["email"]["recipient_count"] == 0
    assert client.delete(f"/api/recipients/{row['id']}").status_code == 200
    monkeypatch.setattr(settings, "email_recipients", ["reader@example.com"])
    init_db(engine)  # Recreating tables / a fresh session must not resurrect env addresses.
    assert client.get("/api/recipients").json()["items"] == []
    new = client.post("/api/recipients", json={"email": "reader@example.com"}).json()
    assert new["id"] > row["id"]


@pytest.mark.parametrize("address", ["x", "a@", "a@@example.com", "a..b@example.com",
                                     "a@-example.com", "a@example.com\r\nBcc:x@example.com"])
def test_invalid_recipient_rejected_without_mutation(recipient_api, address):
    client, _ = recipient_api
    assert client.post("/api/recipients", json={"email": address}).status_code == 422
    assert client.get("/api/recipients").json()["items"] == []


def test_legacy_import_only_once_with_normalization_and_invalid_address_count(
    environment, monkeypatch,
):
    db, _, _ = environment
    monkeypatch.setattr(settings, "email_recipients", [" reader@Example.com ",
                                                      "READER@example.com", "bad-address"])
    assert initialize_recipients(db).skipped_count == 1
    rows = active_recipients(db)
    assert len(rows) == 1 and rows[0].email == "reader@example.com"
    rows[0].enabled = False
    db.commit()
    with Session(db.get_bind()) as reopened:
        initialize_recipients(reopened)
        assert not active_recipients(reopened)
        saved = reopened.get(EmailRecipient, rows[0].id)
        reopened.delete(saved)
        reopened.commit()
    assert not active_recipients(db)
    assert list(db.scalars(select(EmailRecipient))) == []


def add_address(db, address, *, enabled=True):
    result = create_recipient(RecipientInput(email=address, enabled=enabled), db)
    return db.get(EmailRecipient, result["id"])


def setup_recipients(environment, monkeypatch):
    configure_email(monkeypatch)
    monkeypatch.setattr(settings, "email_recipients", [])
    db, _, _ = environment
    first = add_address(db, "first@example.com")
    second = add_address(db, "second@example.com")
    report = run_report(environment)
    approve_report(db, report)
    return db, report, first, second


def test_selected_send_deduplication_disabled_validation_and_history(environment, monkeypatch):
    db, report, first, second = setup_recipients(environment, monkeypatch)
    sent = []

    def sender(message, recipient):
        assert message["To"] == recipient
        assert message.get_body(preferencelist=("html",))
        sent.append(recipient)
        return "sent", None

    for _ in range(2):
        send_report(db, report, recipient_ids=[second.id], sender=sender)
    assert sent == [second.email]
    assert db.scalar(select(EmailDelivery)).attempts == 1
    first.enabled = False
    db.commit()
    with pytest.raises(ValueError, match="停用"):
        send_report(db, report, recipient_ids=[first.id, second.id], sender=sender)
    with pytest.raises(ValueError, match="至少"):
        send_report(db, report, recipient_ids=[], sender=sender)
    db.delete(second)
    db.commit()
    with pytest.raises(ValueError, match="添加"):
        send_report(db, report, sender=sender)
    assert db.scalar(select(EmailDelivery)).recipient == "second@example.com"
    assert sent == ["second@example.com"]


def test_disabling_later_recipient_during_send_skips_it(environment, monkeypatch):
    db, report, first, second = setup_recipients(environment, monkeypatch)
    sent = []

    def sender(message, recipient):
        sent.append(recipient)
        second.enabled = False
        db.commit()
        return "sent", None

    rows = send_report(db, report, sender=sender)
    assert sent == [first.email]
    assert len(rows) == 1


def test_daily_retries_freeze_addresses_and_apply_disabling(environment, monkeypatch):
    db, report, first, second = setup_recipients(environment, monkeypatch)
    attempts = []

    def sender(message, recipient):
        attempts.append(recipient)
        return ("failed", "temporary") if recipient == second.email else ("sent", None)

    dispatch_daily(db, now=START, sender=sender)
    day = report.report_date.isoformat()
    frozen = db.get(DailyEmailAudience, day).recipients_json
    assert {entry["email"] for entry in frozen} == {first.email, second.email}
    third = add_address(db, "third@example.com")
    second.enabled = False
    db.commit()
    with Session(db.get_bind()) as reopened:
        dispatch_daily(reopened, now=START + timedelta(hours=1), sender=sender)
    assert attempts == [first.email, second.email]
    assert third.email not in {entry["email"] for entry in frozen}
    second.enabled = True
    second.email, second.email_key = "changed@example.com", "changed@example.com"
    db.commit()
    dispatch_daily(db, now=START + timedelta(hours=2), sender=sender)
    assert len(attempts) == 2  # Editing an address cannot redirect a frozen retry.


def test_manual_send_does_not_shrink_later_daily_audience(environment, monkeypatch):
    db, report, first, second = setup_recipients(environment, monkeypatch)
    sent = []

    def sender(message, recipient):
        sent.append(recipient)
        return "sent", None

    send_report(db, report, recipient_ids=[first.id], sender=sender)
    dispatch_daily(db, now=START, sender=sender)
    assert sent == [first.email, second.email]
    assert len(db.get(DailyEmailAudience, report.report_date.isoformat()).recipients_json) == 2


def test_automatic_daily_new_recipient_next_day_and_manual_supplement(
    environment, monkeypatch, tmp_path,
):
    db, clock, _ = environment
    configure_daily(monkeypatch, tmp_path, clock)
    job = tick_daily(db, now=START)
    complete_queued(db, job)
    sent = []

    def sender(message, recipient):
        sent.append(recipient)
        return "sent", None

    assert tick_daily(db, now=START, sender=sender).status == "sent"
    new = add_address(db, "new@example.com")
    tick_daily(db, now=START, sender=sender)
    assert sent == ["test@example.com"]
    send_report(db, db.get(DailyReport, job.report_id), recipient_ids=[new.id], sender=sender)
    assert sent == ["test@example.com", new.email]
    clock.time += timedelta(days=1)
    tomorrow = tick_daily(db, now=clock.time)
    complete_queued(db, tomorrow)
    assert tick_daily(db, now=clock.time, sender=sender).status == "sent"
    assert sent == ["test@example.com", new.email, "test@example.com", new.email]


def test_legacy_daily_audience_migration_preserves_prior_targets(environment, monkeypatch):
    db, _, _ = environment
    configure_email(monkeypatch)
    monkeypatch.setattr(settings, "email_recipients", ["first@example.com", "new@example.com"])
    report = run_report(environment)
    approve_report(db, report)
    day = report.report_date.isoformat()
    db.add(DailyDispatch(local_date=day, report_id=report.id))
    db.add(EmailDelivery(report_id=report.id, recipient="first@example.com", status="sent",
                         message_id="<legacy@test>", attempts=1))
    db.commit()
    initialize_recipients(db)
    audience = db.get(DailyEmailAudience, day)
    assert [entry["email"] for entry in audience.recipients_json] == ["first@example.com"]
    sent = []
    dispatch_daily(db, now=START, sender=lambda message, recipient: sent.append(recipient))
    assert sent == []
