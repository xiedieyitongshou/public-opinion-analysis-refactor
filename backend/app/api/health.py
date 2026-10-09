from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    app_name: str
    environment: str


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        environment=settings.app_env,
    )


@router.get("/ready")
def readiness(request: Request):
    coordinator = getattr(request.app.state, "coordinator", None)
    ready = bool(coordinator and coordinator.thread and coordinator.thread.is_alive()
                 and coordinator.last_tick
                 and (datetime.now(UTC) - coordinator.last_tick).total_seconds() < 120
                 and not coordinator.last_error)
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception:
        ready = False
    return JSONResponse({"status": "ready" if ready else "not_ready"},
                        status_code=200 if ready else 503)
