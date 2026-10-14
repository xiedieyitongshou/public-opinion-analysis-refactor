"""Mail configuration persistence, secret handling and runtime delivery changes."""

from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session
from test_email_recipients import recipient_api as recipient_api
from test_email_recipients import setup_recipients
from test_hotspot_analysis import START
from test_hotspot_analysis import environment as environment

from app.core.config import settings
from app.models.briefing import EmailConfiguration
from app.services.briefing_email import dispatch_daily, send_report
from app.services.email_settings import (
    EmailSettingsInput,
    get_email_settings,
    save_email_settings,
)


def payload():
    return EmailSettingsInput(smtp_host="smtp.example.com", email_from="sender@example.com",
                              smtp_username="sender@example.com").model_dump()


def test_configuration_auth_encryption_restart_and_clear(recipient_api, monkeypatch):
    client, engine = recipient_api
    monkeypatch.setattr(settings, "email_settings_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "smtp_password", "old-env-secret")
    assert client.get("/api/email-settings", headers={"Authorization": ""}).status_code == 401
    assert client.put("/api/email-settings", json=payload(),
                      headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/email-settings").json()["source"] == "environment"
    response = client.put("/api/email-settings", json={**payload(), "smtp_password": "new-secret"})
    assert response.status_code == 200
    assert "new-secret" not in response.text and "smtp_password" not in response.json()
    with Session(engine) as db:
        row = db.get(EmailConfiguration, 1)
        assert "new-secret" not in str(row.values_json) + row.password_encrypted
        assert get_email_settings(db).smtp_password == "new-secret"
    assert client.put("/api/email-settings", json=payload()).json()["password_set"]
    assert not client.put("/api/email-settings", json={
        **payload(), "clear_password": True, "smtp_host": "",
    }).json()["password_set"]
    with Session(engine) as db:
        config = get_email_settings(db)
        assert config.smtp_password is None and config.smtp_host == ""
        assert config.source == "database"


@pytest.mark.parametrize("change", [
    {"smtp_port": 0}, {"smtp_security": "invalid"}, {"briefing_timezone": "missing/zone"},
    {"email_send_time": "25:00"}, {"email_from": "x\r\nBcc:evil@example.com"},
    {"smtp_host": "https://smtp.example.com"}, {"smtp_password": ["private-secret"]},
])
def test_invalid_settings_do_not_echo_or_persist(recipient_api, change):
    client, _ = recipient_api
    response = client.put("/api/email-settings", json={**payload(), **change})
    assert response.status_code == 422 and "private-secret" not in response.text
    assert client.get("/api/email-settings").json()["source"] == "environment"


def test_missing_key_and_wrong_key_recovery(recipient_api, monkeypatch):
    client, engine = recipient_api
    monkeypatch.setattr(settings, "email_settings_key", None)
    assert client.put("/api/email-settings", json={
        **payload(), "smtp_password": "secret",
    }).status_code == 422
    monkeypatch.setattr(settings, "email_settings_key", Fernet.generate_key().decode())
    assert client.put("/api/email-settings", json={
        **payload(), "smtp_password": "secret",
    }).status_code == 200
    monkeypatch.setattr(settings, "email_settings_key", Fernet.generate_key().decode())
    with Session(engine) as db, pytest.raises(ValueError, match="解密"):
        get_email_settings(db)
    assert client.get("/api/email-settings").status_code == 200
    assert client.put("/api/email-settings", json={
        **payload(), "smtp_password": "replacement",
    }).status_code == 200


def test_runtime_snapshot_and_reschedule_no_duplicate(environment, monkeypatch):
    db, report, _, _ = setup_recipients(environment, monkeypatch)
    config = EmailSettingsInput(**payload())
    save_email_settings(db, config, clear_password=True)
    sent = []

    def sender(message, recipient):
        sent.append(message["From"])
        save_email_settings(db, config.model_copy(update={"email_from": "changed@example.com"}))
        return "sent", None

    send_report(db, report, sender=sender)
    assert sent == ["sender@example.com", "sender@example.com"]
    config.email_send_time = "00:00"
    save_email_settings(db, config)
    dispatch_daily(db, now=START + timedelta(hours=1), sender=sender)
    assert len(sent) == 2


def test_explicit_diagnostics_use_saved_config(recipient_api, monkeypatch):
    from app.api import email_settings as api

    client, _ = recipient_api
    client.put("/api/email-settings", json={**payload(), "clear_password": True})
    calls = []

    class Connection:
        def close(self):
            calls.append("closed")

    monkeypatch.setattr(api, "smtp_connect", lambda config: Connection())
    monkeypatch.setattr(api, "smtp_send", lambda message, recipient, config: (
        calls.append((recipient, config.email_from)) or "sent", None))
    assert client.post("/api/email-settings/check").json()["status"] == "connected"
    assert calls == ["closed"]
    response = client.post("/api/email-settings/test", json={"recipient": "test@example.com"})
    assert response.json()["status"] == "sent"
    assert calls[-1] == ("test@example.com", "sender@example.com")
    assert client.post("/api/email-settings/test", json={"recipient": "bad"}).status_code == 422
