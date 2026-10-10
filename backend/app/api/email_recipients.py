"""Recipient CRUD for the existing administrator session."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import require_admin
from app.db.session import get_db
from app.models.briefing import EmailDelivery, EmailRecipient
from app.services.email_recipients import initialize_recipients, normalize_address

router = APIRouter(prefix="/api/recipients", dependencies=[Depends(require_admin)],
                   tags=["email recipients"])
Db = Annotated[Session, Depends(get_db)]


class RecipientInput(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    note: str = Field(default="", max_length=120)
    enabled: bool = True

    @field_validator("email")
    @classmethod
    def address(cls, value):
        return normalize_address(value)

    @field_validator("note")
    @classmethod
    def trim_note(cls, value):
        return value.strip()


def recipient_info(row, delivery=None):
    return {
        "id": row.id, "email": row.email, "note": row.note, "enabled": row.enabled,
        "created_at": row.created_at, "updated_at": row.updated_at,
        "last_delivery": ({"report_id": delivery.report_id, "status": delivery.status,
                           "finished_at": delivery.finished_at, "error": delivery.error}
                          if delivery else None),
    }


def get_recipient(db, recipient_id):
    initialize_recipients(db)
    row = db.get(EmailRecipient, recipient_id)
    if row is None:
        raise HTTPException(404, "收件邮箱不存在")
    return row


def save_recipient(db, row):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "该邮箱已存在，请在列表中编辑或启用") from exc
    db.refresh(row)
    return recipient_info(row)


@router.get("")
def recipients(db: Db):
    marker = initialize_recipients(db)
    rows = list(db.scalars(select(EmailRecipient).order_by(EmailRecipient.id)))
    latest_ids = select(func.max(EmailDelivery.id)).where(
        func.lower(EmailDelivery.recipient).in_([row.email_key for row in rows])
    ).group_by(func.lower(EmailDelivery.recipient))
    latest = {row.recipient.casefold(): row for row in db.scalars(
        select(EmailDelivery).where(EmailDelivery.id.in_(latest_ids))
    )}
    return {"items": [recipient_info(row, latest.get(row.email_key)) for row in rows],
            "legacy_import_skipped": marker.skipped_count}


@router.post("", status_code=201)
def create_recipient(data: RecipientInput, db: Db):
    initialize_recipients(db)
    now = datetime.now(UTC)
    row = EmailRecipient(email=data.email, email_key=data.email.casefold(), note=data.note,
                         enabled=data.enabled, created_at=now, updated_at=now)
    db.add(row)
    return save_recipient(db, row)


@router.put("/{recipient_id}")
def edit_recipient(recipient_id: int, data: RecipientInput, db: Db):
    row = get_recipient(db, recipient_id)
    row.email, row.email_key = data.email, data.email.casefold()
    row.note, row.enabled, row.updated_at = data.note, data.enabled, datetime.now(UTC)
    return save_recipient(db, row)


@router.delete("/{recipient_id}")
def delete_recipient(recipient_id: int, db: Db):
    row = get_recipient(db, recipient_id)
    db.delete(row)
    db.commit()
    return {"deleted": True}
