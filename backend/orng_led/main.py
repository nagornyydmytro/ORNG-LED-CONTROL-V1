"""FastAPI entrypoint: health API and optional production SPA hosting."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from orng_led import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

app = FastAPI(
    title="ORNG LED CONTROL",
    version=__version__,
    docs_url=None,
    redoc_url=None,
)


@app.get("/api/health")
def health() -> dict[str, object]:
    """Basic readiness probe used by scripts and the scaffold UI."""
    return {
        "status": "ok",
        "service": "orng-led-control",
        "version": __version__,
        "transport": "mock",
        "output_armed": False,
        "artnet_network_enabled": False,
        "frontend_dist_present": FRONTEND_DIST.is_dir(),
    }


def _register_frontend() -> None:
    """Serve the Vite production build when frontend/dist exists."""
    if not FRONTEND_DIST.is_dir():
        return

    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    async def index() -> FileResponse:
        index_path = FRONTEND_DIST / "index.html"
        if not index_path.is_file():
            raise HTTPException(status_code=503, detail="Frontend build missing index.html")
        return FileResponse(index_path)

    @app.get("/{full_path:path}")
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


_register_frontend()
