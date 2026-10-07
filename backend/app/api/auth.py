"""Single-admin access control, with a short-lived HttpOnly browser session."""

import hashlib
import hmac
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.core.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


def check_origin(request):
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    if origin != expected:
        raise HTTPException(403, "浏览器操作必须来自本站页面")


def session_value(expires):
    signature = hmac.new(
        (settings.admin_token or "").encode(), str(expires).encode(), hashlib.sha256
    ).hexdigest()
    return f"{expires}.{signature}"


def require_admin(request: Request):
    if not settings.admin_token:
        raise HTTPException(503, "请先在本地环境中配置 ADMIN_TOKEN")
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer ") and hmac.compare_digest(
        authorization[7:], settings.admin_token
    ):
        return "admin"
    cookie = request.cookies.get("briefing_session", "")
    try:
        expires = int(cookie.split(".", 1)[0])
        valid = time.time() < expires and hmac.compare_digest(cookie, session_value(expires))
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(401, "请登录管理后台")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
    return "admin"


class LoginInput(BaseModel):
    token: str = Field(min_length=1, max_length=500)


@router.post("/session")
def login(data: LoginInput, request: Request, response: Response):
    check_origin(request)
    if not settings.admin_token:
        raise HTTPException(503, "请先在 backend/.env 配置 ADMIN_TOKEN，再启动服务")
    if not hmac.compare_digest(data.token, settings.admin_token):
        raise HTTPException(401, "管理口令不正确")
    response.set_cookie(
        "briefing_session",
        session_value(int(time.time()) + 8 * 3600),
        max_age=8 * 3600,
        httponly=True,
        samesite="strict",
        secure=settings.admin_cookie_secure,
    )
    return {"status": "ok"}


@router.post("/logout")
def logout(request: Request, response: Response):
    check_origin(request)
    response.delete_cookie("briefing_session")
    return {"status": "ok"}
