from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import api_router
from app.core.config import settings
from app.db.init_db import init_db
from app.services.briefing_jobs import JobCoordinator


@asynccontextmanager
async def lifespan(app):
    init_db()
    coordinator = JobCoordinator()
    app.state.coordinator = coordinator
    coordinator.start()
    try:
        yield
    finally:
        coordinator.stop()


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
