from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import api_router
from app.core.config import settings
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.services.briefing_jobs import JobCoordinator
from app.services.email_recipients import initialize_recipients
from app.services.runtime_logging import configure_logging


@asynccontextmanager
async def lifespan(app):
    logger = configure_logging()
    if settings.app_env == "production":
        if not settings.admin_token or len(settings.admin_token) < 32:
            raise RuntimeError("Production requires ADMIN_TOKEN with at least 32 characters")
        if settings.matching_profile != "rules":
            from app.services.semantic_models import DEFAULT_MODEL_ROOT

            root = Path(settings.semantic_model_dir or DEFAULT_MODEL_ROOT)
            required = [root / "embedding" / "config.json"]
            if settings.matching_profile == "hybrid_rerank":
                required.append(root / "reranker" / "onnx" / "model_quantized.onnx")
            if not all(path.is_file() for path in required):
                raise RuntimeError("Prepare and mount the local semantic models before startup")
    init_db()
    with SessionLocal() as db:
        initialize_recipients(db)
    coordinator = JobCoordinator()
    app.state.coordinator = coordinator
    coordinator.start()
    logger.info("service_started scheduler=%s daily=%s", settings.scheduler_enabled,
                settings.daily_briefing_enabled)
    try:
        yield
    finally:
        coordinator.stop()
        logger.info("service_stopped")


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
    app.include_router(api_router)
    static = Path(__file__).parent / "web"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; frame-ancestors 'self'; base-uri 'none'; form-action 'self'"
        )
        if request.url.path.startswith(("/api", "/ops", "/auth")):
            response.headers["Cache-Control"] = "no-store"
        return response

    return app


app = create_app()
