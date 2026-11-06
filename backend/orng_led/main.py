"""FastAPI entrypoint: API, WebSocket, runtime lifecycle, SPA hosting."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from orng_led import __version__
from orng_led.api.routes import build_api_router
from orng_led.api.runtime import AppRuntime, CommandConflictError, ManualShowLockedError
from orng_led.api.ws import build_ws_router

REPO_ROOT = Path(__file__).resolve().parents[2]
# Backward-compatible name for tests/docs (developer checkout default).
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


def resolve_frontend_dist() -> Path:
    """Release-relative frontend first, then env, then developer checkout."""
    override = os.environ.get("ORNG_FRONTEND_DIST", "").strip()
    if override:
        return Path(override)
    # Packaged release: orng_led lives next to frontend_dist/
    pkg_root = Path(__file__).resolve().parents[1]
    release_dist = pkg_root.parent / "frontend_dist"
    if release_dist.is_dir():
        return release_dist
    # Same directory layout when cwd is the release root.
    cwd_dist = Path.cwd() / "frontend_dist"
    if cwd_dist.is_dir():
        return cwd_dist
    return REPO_ROOT / "frontend" / "dist"


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
        # Finalize interrupted updates before accepting traffic.
        try:
            from orng_led.updater.service import AppUpdateService

            AppUpdateService(active).recover_after_crash()
        except Exception:  # noqa: BLE001
            pass
        await active.start()
        from orng_led.desktop.remote_desk import watch_executor

        watcher = asyncio.create_task(watch_executor(active))
        try:
            yield
        finally:
            watcher.cancel()
            await active.shutdown()

    application = FastAPI(
        title="ORNG LED CONTROL",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @application.exception_handler(ManualShowLockedError)
    async def manual_show_locked(_request: Request, exc: ManualShowLockedError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "status": "rejected"},
        )

    @application.exception_handler(CommandConflictError)
    async def command_conflict(_request: Request, exc: CommandConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "status": "rejected", "error_code": exc.code},
        )
    frontend_dist = resolve_frontend_dist()
    application.state.frontend_dist = frontend_dist
    application.state.frontend_dist_present = frontend_dist.is_dir()
    if runtime is not None:
        application.state.runtime = runtime

    @application.middleware("http")
    async def remote_executor_commands(request, call_next):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        from orng_led.desktop.desk_role import desk_role
        from orng_led.desktop.remote_desk import (
            QUICK_COMMANDS,
            RemoteDeskError,
            command_for_request,
            desk_source_for,
            forward_operator_command,
        )

        runtime = getattr(request.app.state, "runtime", None)
        path = request.url.path
        operator = (
            path.startswith("/api/commands/")
            or path.startswith("/api/input/")
            or path.startswith("/api/output/")
        )
        if (
            runtime is not None
            and request.method == "POST"
            and operator
            and desk_role(runtime.config_root) == "raspberry"
        ):
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                body = {}
            if not isinstance(body, dict):
                body = {}
            try:
                mapped = command_for_request(path, body)
            except (ValueError, KeyError) as exc:
                return JSONResponse({"detail": str(exc)}, status_code=400)
            if mapped is None:
                return JSONResponse(
                    {"detail": "Ця дія лишається локальною і в режимі Raspberry не виконується."},
                    status_code=409,
                )
            command_type, payload = mapped
            if command_type == "block":
                return JSONResponse(
                    {"detail": "Локальний Art-Net вимкнено. Світло цієї локації йде з Raspberry."},
                    status_code=409,
                )
            if command_type == "blackout_toggle":
                view = getattr(runtime, "executor_view", None)
                if not isinstance(view, dict) or "blackout" not in view:
                    return JSONResponse(
                        {"detail": "Стан blackout виконавця ще невідомий. Команду не надіслано."},
                        status_code=409,
                    )
                command_type = "blackout"
                payload = {**payload, "enabled": not bool(view.get("blackout"))}
            from orng_led.desktop.remote_desk import executor_supports

            if command_type != "close_session" and not executor_supports(
                command_type, getattr(runtime, "executor_view", None)
            ):
                return JSONResponse(
                    {
                        "detail": (
                            "Встановлений виконавець не підтримує цю команду. "
                            "Оновлення застосунку потрібне окремим вікном."
                        )
                    },
                    status_code=409,
                )
            if body.get("client_command_id") and "command_id" not in payload:
                payload = {**payload, "command_id": str(body["client_command_id"])}
            if not str(payload.get("source") or "").startswith("cloud:desk:"):
                payload = {**payload, "source": desk_source_for(body)}
            try:
                forwarded = await forward_operator_command(command_type, payload)
            except RemoteDeskError as exc:
                return JSONResponse({"detail": exc.message}, status_code=exc.status)
            state = runtime.build_state().model_dump(mode="json")
            status = str(forwarded.get("status") or "unknown")
            applied = status == "applied" and forwarded.get("ok", True) is not False
            # accepted is not a finished apply. Holds return as soon as the
            # session takes the frame so release is not stuck behind press ACK.
            quiet = status == "accepted" and command_type in QUICK_COMMANDS
            if applied or quiet or command_type == "close_session":
                http_status = 200
            elif status == "failed":
                http_status = 502
            else:
                http_status = 504
            return JSONResponse(
                {
                    "ok": applied,
                    "accepted": applied,
                    "idempotent_replay": bool(forwarded.get("idempotent_replay")),
                    "reason": "executor" if applied else (None if quiet else status),
                    "command_status": status,
                    "command_id": forwarded.get("command_id"),
                    "state": state,
                },
                status_code=http_status,
            )
        return await call_next(request)

    application.include_router(build_api_router())
    application.include_router(build_ws_router())
    _register_frontend(application, frontend_dist)
    return application


def _register_frontend(application: FastAPI, frontend_dist: Path) -> None:
    """Serve the Vite production build when frontend dist exists."""
    if not frontend_dist.is_dir():
        return

    assets_dir = frontend_dist / "assets"
    if assets_dir.is_dir():
        application.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @application.get("/")
    async def index() -> FileResponse:
        index_path = frontend_dist / "index.html"
        if not index_path.is_file():
            raise HTTPException(status_code=503, detail="Frontend build missing index.html")
        return FileResponse(
            index_path,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )

    @application.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        # Never SPA-fallback asset paths: HTML-as-JS causes a blank dark page.
        if full_path.startswith("assets/") or full_path == "assets":
            raise HTTPException(status_code=404, detail="Asset not found")

        candidate = frontend_dist / full_path
        if candidate.is_file():
            return FileResponse(candidate)

        index_path = frontend_dist / "index.html"
        if not index_path.is_file():
            raise HTTPException(status_code=503, detail="Frontend build missing index.html")
        return FileResponse(
            index_path,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )


app = create_app()
