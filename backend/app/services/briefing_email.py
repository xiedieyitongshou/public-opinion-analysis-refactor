"""Approval-gated SMTP with durable per-version/per-recipient idempotency."""

from __future__ import annotations

import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, parseaddr
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models import DailyReport
from app.models.briefing import DailyBriefingJob, DailyDispatch, EmailDelivery
from app.schemas.display import DailyBriefing
from app.services.briefing import as_utc, review_briefing
from app.services.briefing_render import render_report, render_text


def valid_address(address):
    return bool(
        address
        and "\r" not in address
        and "\n" not in address
        and parseaddr(address)[1] == address
        and address.count("@") == 1
        and all(address.split("@"))
    )


def email_ready():
    return bool(
        settings.smtp_host
        and valid_address(settings.email_from)
        and settings.email_recipients
        and all(valid_address(value) for value in settings.email_recipients)
    )


def build_message(report, recipient, message_id):
    briefing = DailyBriefing.model_validate(report.briefing_json)
    message = EmailMessage(policy=SMTP)
    message["Subject"] = report.title
    message["From"], message["To"] = settings.email_from, recipient
    message["Message-ID"] = message_id
    message["Date"] = format_datetime(datetime.now(UTC))
    message.set_content(render_text(briefing))
    message.add_alternative(render_report(briefing, email=True, status="approved"), subtype="html")
    return message


def smtp_send(message, recipient):
    """Only a final successful DATA reply means accepted by the SMTP server."""
    connection, phase = None, "connect"
    try:
        if settings.smtp_security == "ssl":
            connection = smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=settings.smtp_timeout_seconds,
                context=ssl.create_default_context(),
            )
        else:
            connection = smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds
            )
        connection.ehlo()
        if settings.smtp_security == "starttls":
            connection.starttls(context=ssl.create_default_context())
            connection.ehlo()
        if settings.smtp_username:
            connection.login(settings.smtp_username, settings.smtp_password or "")
        code, _ = connection.mail(settings.email_from)
        if code != 250:
            return "failed", f"SMTP MAIL rejected ({code})"
        code, _ = connection.rcpt(recipient)
        if code not in {250, 251}:
            return "failed", f"SMTP RCPT rejected ({code})"
        phase = "data"
        code, _ = connection.data(message.as_bytes())
        return ("sent", None) if code == 250 else ("failed", f"SMTP DATA rejected ({code})")
    except smtplib.SMTPResponseException as exc:
        return "failed", f"{type(exc).__name__} ({exc.smtp_code})"
    except (OSError, smtplib.SMTPException) as exc:
        return ("unknown" if phase == "data" else "failed"), type(exc).__name__
    finally:
        if connection is not None:
            connection.close()


def recover_interrupted_deliveries(db, now=None):
    now = now or datetime.now(UTC)
    db.execute(
        update(EmailDelivery)
        .where(
            EmailDelivery.status == "sending",
            EmailDelivery.started_at
            < now - timedelta(seconds=settings.smtp_timeout_seconds * 6 + 60),
        )
        .values(status="unknown", error="进程在投递过程中中断，需要人工核对收件箱")
    )
    db.commit()


def reconcile_sent_report(db, report_id):
    rows = db.scalars(select(EmailDelivery).where(EmailDelivery.report_id == report_id)).all()
    if rows and all(row.status == "sent" for row in rows):
        report = db.get(DailyReport, report_id)
        report.status = "sent"
        db.execute(update(DailyBriefingJob).where(
            DailyBriefingJob.report_id == report_id,
        ).values(status="sent", error=None))
        db.commit()


def send_report(db, report, *, sender=smtp_send, automatic=False):
    if report.status not in {"approved", "sent"}:
        raise ValueError("只有已确认的日报版本可以发送")
    if review_briefing(DailyBriefing.model_validate(report.briefing_json))["blocks_publish"]:
        raise ValueError("日报质量检查阻断发送")
    if not email_ready():
        raise ValueError("尚未配置 SMTP、发件人和有效收件人；仍可预览邮件")
    recover_interrupted_deliveries(db)
    deliveries = []
    for recipient in dict.fromkeys(settings.email_recipients):
        row = db.scalar(
            select(EmailDelivery).where(
                EmailDelivery.report_id == report.id, EmailDelivery.recipient == recipient
            )
        )
        if row is None:
            row = EmailDelivery(
                report_id=report.id,
                recipient=recipient,
                status="pending",
                attempts=0,
                message_id=f"<{uuid4()}@briefing.local>",
            )
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                row = db.scalar(
                    select(EmailDelivery).where(
                        EmailDelivery.report_id == report.id, EmailDelivery.recipient == recipient
                    )
                )
        deliveries.append(row)
        if row.status not in {"pending", "failed"} or row.attempts >= settings.email_max_attempts:
            continue
        now = datetime.now(UTC)
        if (
            automatic
            and row.finished_at
            and now - as_utc(row.finished_at) < timedelta(minutes=5 * 2 ** max(0, row.attempts - 1))
        ):
            continue
        claimed = db.execute(
            update(EmailDelivery)
            .where(
                EmailDelivery.id == row.id,
                EmailDelivery.status.in_(["pending", "failed"]),
                EmailDelivery.attempts == row.attempts,
            )
            .values(status="sending", started_at=now, attempts=row.attempts + 1)
        )
        db.commit()
        if not claimed.rowcount:
            continue
        db.refresh(row)
        try:
            row.status, row.error = sender(
                build_message(report, recipient, row.message_id), recipient
            )
        except Exception as exc:
            row.status, row.error = "unknown", type(exc).__name__
        row.finished_at = datetime.now(UTC)
        db.commit()
    if deliveries and all(row.status == "sent" for row in deliveries):
        report.status = "sent"
        db.commit()
        reconcile_sent_report(db, report.id)
    return deliveries


def dispatch_daily(db, *, now=None, sender=smtp_send):
    now = now or datetime.now(UTC)
    local = now.astimezone(ZoneInfo(settings.briefing_timezone))
    if local.strftime("%H:%M") < settings.email_send_time or not email_ready():
        return []
    day = local.date().isoformat()
    dispatch = db.get(DailyDispatch, day)
    if dispatch:
        report = db.get(DailyReport, dispatch.report_id)
    else:
        report = db.scalar(
            select(DailyReport)
            .where(
                DailyReport.report_date == local.date(),
                DailyReport.status.in_(["approved", "sent"]),
            )
            .order_by(DailyReport.id.desc())
        )
        if report is None:
            return []
        db.add(DailyDispatch(local_date=day, report_id=report.id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            report = db.get(DailyReport, db.get(DailyDispatch, day).report_id)
    return send_report(db, report, sender=sender, automatic=True)
