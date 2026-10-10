"""Administrator-managed recipients and durable audiences for daily delivery."""

import re
from datetime import UTC, datetime
from email.utils import parseaddr

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models.briefing import (
    DailyDispatch,
    DailyEmailAudience,
    EmailDelivery,
    EmailRecipient,
    EmailRecipientInitialization,
)


def normalize_address(value: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError("邮箱格式不正确")
    address = value.strip()
    if len(address) > 320 or parseaddr(address)[1] != address or address.count("@") != 1:
        raise ValueError("邮箱格式不正确")
    local, domain = address.rsplit("@", 1)
    try:
        domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("邮箱域名不正确") from exc
    if (
        not local or len(local) > 64
        or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+", local)
        or local.startswith(".") or local.endswith(".") or ".." in local
        or "." not in domain or len(domain) > 253
        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
               for label in domain.split("."))
    ):
        raise ValueError("邮箱格式不正确")
    return f"{local}@{domain}"


def initialize_recipients(db):
    """Import legacy env addresses exactly once, including an intentionally empty list."""
    marker = db.get(EmailRecipientInitialization, 1)
    if marker is not None:
        return marker
    now, skipped = datetime.now(UTC), 0
    known = set(db.scalars(select(EmailRecipient.email_key)))
    for value in settings.email_recipients:
        try:
            address = normalize_address(value)
        except ValueError:
            skipped += 1
            continue
        key = address.casefold()
        if key not in known:
            db.add(EmailRecipient(email=address, email_key=key, note="",
                                  enabled=True, created_at=now, updated_at=now))
            known.add(key)
    db.add(EmailRecipientInitialization(id=1, imported_at=now, skipped_count=skipped))
    try:
        db.flush()
        # Preserve already-started legacy daily deliveries during the one-time migration.
        recipients = list(db.scalars(select(EmailRecipient)))
        for dispatch in db.scalars(select(DailyDispatch)):
            prior = set(db.scalars(select(func.lower(EmailDelivery.recipient))
                                  .where(EmailDelivery.report_id == dispatch.report_id)))
            if prior and db.get(DailyEmailAudience, dispatch.local_date) is None:
                db.add(DailyEmailAudience(
                    local_date=dispatch.local_date, report_id=dispatch.report_id,
                    recipients_json=[recipient_snapshot(row) for row in recipients
                                     if row.email_key in prior], created_at=now,
                ))
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.get(EmailRecipientInitialization, 1) is None:
            raise
    return db.get(EmailRecipientInitialization, 1)


def active_recipients(db):
    initialize_recipients(db)
    return list(db.scalars(select(EmailRecipient).where(EmailRecipient.enabled.is_(True))
                           .order_by(EmailRecipient.id).execution_options(populate_existing=True)))


def recipient_snapshot(row):
    return {"id": row.id, "email": row.email}


def selected_recipients(db, recipient_ids=None):
    active = active_recipients(db)
    if recipient_ids is not None:
        requested = set(recipient_ids)
        if not requested:
            raise ValueError("请至少选择一位收件人")
        if requested - {row.id for row in active}:
            raise ValueError("部分收件人已停用或删除，请刷新后重新选择")
        active = [row for row in active if row.id in requested]
    if not active:
        raise ValueError("请先添加并启用收件邮箱")
    return [recipient_snapshot(row) for row in active]


def eligible_audience(db, audience):
    active = {row.id: row.email for row in active_recipients(db)}
    return [entry for entry in audience if active.get(entry["id"]) == entry["email"]]


def freeze_daily_audience(db, day, report_id):
    row = db.get(DailyEmailAudience, day)
    if row is None:
        recipients = [recipient_snapshot(value) for value in active_recipients(db)]
        if not recipients:
            return []
        row = DailyEmailAudience(local_date=day, report_id=report_id,
                                 recipients_json=recipients, created_at=datetime.now(UTC))
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            row = db.get(DailyEmailAudience, day)
    if row.report_id != report_id:
        raise ValueError("当天定时投递已固定其他日报版本")
    return eligible_audience(db, row.recipients_json)
