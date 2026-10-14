"""Database overrides with encrypted credentials and immutable operation snapshots."""

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.models.briefing import EmailConfiguration
from app.services.email_recipients import normalize_address


class EmailSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    smtp_host: str = Field(default="", max_length=253)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_security: str = "ssl"
    smtp_username: str = Field(default="", max_length=320)
    email_from: str = ""
    smtp_timeout_seconds: int = Field(default=20, ge=1, le=60)
    email_max_attempts: int = Field(default=3, ge=1, le=5)
    daily_briefing_enabled: bool = False
    email_schedule_enabled: bool = False
    email_send_time: str = Field(default="08:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    briefing_timezone: str = "Asia/Shanghai"

    @field_validator("smtp_host", "smtp_username")
    @classmethod
    def clean(cls, value):
        if any(c.isspace() for c in value) or "://" in value or "/" in value:
            raise ValueError("请输入主机名或账号，不含空白或 URL 前缀")
        return value

    @field_validator("smtp_security")
    @classmethod
    def security(cls, value):
        if value not in {"ssl", "starttls", "plain"}:
            raise ValueError("无效的加密方式")
        if value == "plain" and settings.app_env == "production":
            raise ValueError("生产环境必须使用 SSL 或 STARTTLS")
        return value

    @field_validator("email_from")
    @classmethod
    def address(cls, value):
        return normalize_address(value) if value else ""

    @field_validator("briefing_timezone")
    @classmethod
    def timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("无效时区") from exc
        return value


FIELDS = tuple(EmailSettingsInput.model_fields)


def cipher():
    try:
        return Fernet((settings.email_settings_key or "").encode())
    except (ValueError, TypeError) as exc:
        raise ValueError("需在部署环境配置有效的 EMAIL_SETTINGS_KEY，不能使用后台口令代替") from exc


def get_email_settings(db=None, *, decrypt=True):
    row = db.get(EmailConfiguration, 1, populate_existing=True) if db is not None else None
    values = dict(row.values_json) if row else {key: getattr(settings, key) for key in FIELDS}
    password = settings.smtp_password if row is None else None
    if row and row.password_encrypted and decrypt:
        try:
            password = cipher().decrypt(row.password_encrypted.encode()).decode()
        except InvalidToken as exc:
            raise ValueError(
                "邮件凭据无法解密，请恢复原 EMAIL_SETTINGS_KEY 或重新保存授权码"
            ) from exc
    return SimpleNamespace(
        **values,
        smtp_password=password,
        source="database" if row else "environment",
        password_set=bool(row.password_encrypted if row else password),
    )


def public_settings(db):
    config = get_email_settings(db, decrypt=False)
    result = {key: getattr(config, key) for key in FIELDS}
    result.update(
        source=config.source,
        password_set=config.password_set,
        encryption_ready=bool(settings.email_settings_key),
    )
    return result


def save_email_settings(db, values, password=None, clear_password=False):
    # Unchanged secret is copied as ciphertext; replacing it can recover from a lost key.
    row = db.get(EmailConfiguration, 1)
    encrypted = row.password_encrypted if row else None
    if clear_password:
        encrypted = None
    elif password is not None:
        encrypted = cipher().encrypt(password.encode()).decode() if password else None
    elif row is None and settings.smtp_password:
        encrypted = cipher().encrypt(settings.smtp_password.encode()).decode()
    if row is None:
        row = EmailConfiguration(id=1)
        db.add(row)
    row.values_json = values.model_dump()
    row.password_encrypted = encrypted
    row.updated_at = datetime.now(UTC)
    db.commit()
    return public_settings(db)
