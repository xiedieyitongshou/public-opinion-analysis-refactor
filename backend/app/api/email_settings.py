"""Administrator-only mail configuration and explicit diagnostic actions."""

from email.message import EmailMessage

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.api.auth import require_admin
from app.api.email_recipients import Db
from app.services.briefing_email import smtp_connect, smtp_send
from app.services.email_recipients import normalize_address
from app.services.email_settings import (
    EmailSettingsInput,
    get_email_settings,
    public_settings,
    save_email_settings,
)

router = APIRouter(prefix="/api/email-settings", dependencies=[Depends(require_admin)])


@router.get("")
def read_settings(db: Db):
    return public_settings(db)


@router.put("")
async def update_settings(request: Request, db: Db):
    try:
        data = await request.json()
        password = data.pop("smtp_password", None)
        clear = data.pop("clear_password", False)
        if (
            password is not None
            and (not isinstance(password, str) or len(password) > 4096)
            or not isinstance(clear, bool)
            or (clear and password)
        ):
            raise ValueError("授权码操作无效")
        values = EmailSettingsInput.model_validate(data)
        return save_email_settings(db, values, password, clear)
    except ValidationError as exc:
        # Never return submitted credential values in validation errors.
        fields = ", ".join(str(e["loc"][0]) for e in exc.errors())
        raise HTTPException(422, f"邮件配置格式错误，请检查：{fields}") from None
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, "保存失败，请检查输入与 EMAIL_SETTINGS_KEY 配置") from None


@router.post("/check")
def check_connection(db: Db):
    connection = None
    try:
        config = get_email_settings(db)
        if not config.smtp_host:
            raise ValueError("missing host")
        connection = smtp_connect(config)
        return {"status": "connected", "message": "连接与认证成功，尚未发送邮件"}
    except Exception as exc:
        return {"status": "failed", "message": type(exc).__name__}
    finally:
        if connection:
            connection.close()


@router.post("/test")
async def test_delivery(request: Request, db: Db):
    try:
        data = await request.json()
        recipient = normalize_address(data["recipient"])
        config = get_email_settings(db)
        normalize_address(config.email_from)
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, "请检查测试地址、发件地址及凭据加密配置") from None
    message = EmailMessage()
    message["Subject"] = "舆情日报：邮件配置测试"
    message["From"], message["To"] = config.email_from, recipient
    message.set_content("这是一封管理员主动发送的配置测试邮件，不包含日报或收件人名单。")
    status, error = await run_in_threadpool(smtp_send, message, recipient, config=config)
    return {"status": status, "message": error or "SMTP 已接受，请核对收件箱和垃圾箱"}
