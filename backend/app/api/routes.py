"""API route registration."""

from fastapi import APIRouter, Depends

from app.api.auth import require_admin
from app.api.auth import router as auth_router
from app.api.briefing import router as briefing_router
from app.api.email_recipients import router as recipients_router
from app.api.health import router as health_router
from app.api.ops import router as ops_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(ops_router, dependencies=[Depends(require_admin)])
api_router.include_router(auth_router)
api_router.include_router(briefing_router)
api_router.include_router(recipients_router)
