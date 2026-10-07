"""Protocol and process-boundary checks for SMTP and the single-instance scheduler."""

import smtplib
import socketserver
import threading
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.init_db import init_db
from app.models.briefing import AnalysisRun
from app.services.briefing_email import smtp_send
from app.services.briefing_jobs import JobCoordinator, default_analysis_input, queue_analysis


@pytest.mark.parametrize(
    "mode, expected", [("ok", "sent"), ("refuse", "failed"), ("drop_after_data", "unknown")]
)
def test_real_smtp_conversation_with_local_server(monkeypatch, mode, expected):
    messages = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b"220 local-test ESMTP\r\n")
            while line := self.rfile.readline():
                command = line.decode().split(" ", 1)[0].strip().upper()
                if command == "EHLO":
                    self.wfile.write(b"250 local-test\r\n")
                elif command == "MAIL":
                    self.wfile.write(b"250 sender ok\r\n")
                elif command == "RCPT":
                    self.wfile.write(b"550 rejected\r\n" if mode == "refuse" else b"250 ok\r\n")
                elif command == "DATA":
                    self.wfile.write(b"354 continue\r\n")
                    content = []
                    while (part := self.rfile.readline()) not in (b".\r\n", b""):
                        content.append(part)
                    messages.append(b"".join(content))
                    if mode == "drop_after_data":
                        return
                    self.wfile.write(b"250 accepted\r\n")
                else:
                    return

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
        monkeypatch.setattr(settings, "smtp_port", server.server_address[1])
        monkeypatch.setattr(settings, "smtp_security", "plain")
        monkeypatch.setattr(settings, "smtp_username", None)
        monkeypatch.setattr(settings, "email_from", "sender@example.com")
        message = EmailMessage()
        message["From"], message["To"] = settings.email_from, "receiver@example.com"
        message["Subject"] = "local protocol acceptance"
        message.set_content("Only a local SMTP test.\n")
        try:
            status, error = smtp_send(message, "receiver@example.com")
            assert status == expected, error
            assert bool(messages) is (mode != "refuse")
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_starttls_failure_is_definite_failure_before_data(monkeypatch):
    class RefusingTLS:
        def __init__(self, *args, **kwargs):
            pass

        def ehlo(self):
            pass

        def starttls(self, **kwargs):
            raise smtplib.SMTPNotSupportedError("STARTTLS unavailable")

        def close(self):
            pass

    monkeypatch.setattr(settings, "smtp_security", "starttls")
    monkeypatch.setattr(smtplib, "SMTP", RefusingTLS)
    assert smtp_send(EmailMessage(), "test@example.com")[0] == "failed"


def test_coordinator_singleton_timeout_and_restart_recovery(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    init_db(engine)
    factory = sessionmaker(engine)
    launched = []

    class Process:
        def __init__(self, *args, **kwargs):
            self.done = False
            launched.append(self)

        def poll(self):
            return 1 if self.done else None

        def terminate(self):
            self.done = True

        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr("app.services.briefing_jobs.subprocess.Popen", Process)
    with factory() as db:
        first = queue_analysis(db, default_analysis_input("single"))
        duplicate = queue_analysis(db, default_analysis_input("second-click"))
        assert first.run_id == duplicate.run_id
    one, two = JobCoordinator(factory), JobCoordinator(factory)
    one.tick()
    two.tick()
    one.tick()
    assert len(launched) == 1
    one.process_started -= settings.job_timeout_seconds + 1
    one.tick()
    with factory() as db:
        row = db.get(AnalysisRun, "single")
        assert row.status == "failed" and row.error == "wall_clock_timeout"
        row = queue_analysis(db, default_analysis_input("orphan"))
        row.status = "running"
        row.started_at = datetime.now(UTC) - timedelta(seconds=settings.job_timeout_seconds + 90)
        db.commit()
    one.stop()
    two.tick()
    with factory() as db:
        assert db.get(AnalysisRun, "orphan").error == "interrupted_worker"
    two.stop()
    engine.dispose()
