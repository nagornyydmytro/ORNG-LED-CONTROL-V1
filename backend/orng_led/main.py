"""FastAPI entrypoint: API, WebSocket, runtime lifecycle, SPA hosting."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from orng_led import __version__
from orng_led.api.routes import build_api_router
from orng_led.api.runtime import AppRuntime
from orng_led.api.ws import build_ws_router

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


def create_app(
    *,
    runtime: AppRuntime | None = None,
    autostart_loop: bool = True,
) -> FastAPI:
    """Application factory used by uvicorn and tests."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active = getattr(app.state, "runtime", None)
        if active is None:
            active = AppRuntime.create(autostart_loop=autostart_loop)
            app.state.runtime = active
        await active.start()
        try:
            yield
        finally:
            await active.shutdown()

    application = FastAPI(
        title="ORNG LED CONTROL",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.frontend_dist_present = FRONTEND_DIST.is_dir()
    if runtime is not None:
        application.state.runtime = runtime

    application.include_router(build_api_router())
    application.include_router(build_ws_router())
    _register_frontend(application)
    return application


def _register_frontend(application: FastAPI) -> None:
    """Serve the Vite production build when frontend/dist exists."""
    if not FRONTEND_DIST.is_dir():
        return

    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        application.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @application.get("/")
    async def index() -> FileResponse:
        index_path = FRONTEND_DIST / "index.html"
        if not index_path.is_file():
            raise HTTPException(status_code=503, detail="Frontend build missing index.html")
        return FileResponse(index_path)

    @application.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")

        candidate = FRONTEND_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)

        index_path = FRONTEND_DIST / "index.html"
        if not index_path.is_file():
            raise HTTPException(status_code=503, detail="Frontend build missing index.html")
        return FileResponse(index_path)


app = create_app()
