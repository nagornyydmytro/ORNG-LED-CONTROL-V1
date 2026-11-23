"""Protected desk for the venue LAN.

The show engine keeps listening on 127.0.0.1. This is a second socket in the
same process: HTTPS, one operator role, and only the commands the desk already
sends to AppRuntime. It does not bind every interface and it does not own a
second Art-Net output.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocket

from orng_led.api.routes import build_api_router
from orng_led.api.runtime import AppRuntime
from orng_led.api.ws import build_ws_router

logger = logging.getLogger(__name__)

COOKIE = "orng_lan"
SESSION_TTL_S = 12 * 60 * 60
FAIL_WINDOW_S = 15 * 60
MAX_FAILURES = 8
_ARTNET_NET = ipaddress.ip_network("2.0.0.0/8")
_TAB_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1

# Operator show controls. Setup, role, output, pairing, config and updates are absent.
_GET = {
    "/api/health",
    "/api/state",
    "/api/ready",
    "/api/presets",
    "/api/stage/layout",
    "/api/scenes",
    "/api/scenes/active",
    "/api/auto/catalog",
    "/api/input/status",
    "/api/input/mapping",
    "/api/lan/session",
}
_POST = {
    "/api/commands/blackout",
    "/api/commands/select-preset",
    "/api/commands/seek-episode",
    "/api/commands/face",
    "/api/commands/master-brightness",
    "/api/commands/head-desk",
    "/api/commands/live-fx-speed",
    "/api/commands/bpm-sync",
    "/api/commands/auto-next-look",
    "/api/commands/preview-speed",
    "/api/commands/white-hit",
    "/api/commands/strobe",
    "/api/commands/drop",
    "/api/commands/color-hit",
    "/api/commands/sweep",
    "/api/commands/sweep-hit",
    "/api/commands/pad-presets",
    "/api/commands/focus-loss",
    "/api/commands/visibility-hidden",
    "/api/input/action",
    "/api/input/button",
    "/api/input/keyboard",
    "/api/lan/logout",
}
_STAMP = {
    "/api/input/action",
    "/api/input/button",
    "/api/input/keyboard",
}


class LanConfigError(ValueError):
    pass


@dataclass
class LanConfig:
    host: str
    port: int
    allowed_hosts: set[str]
    origins: set[str]
    secret_path: Path
    certfile: str
    keyfile: str
    allow_http: bool = False

    @classmethod
    def from_env(cls, config_root: Path) -> LanConfig:
        if os.environ.get("ORNG_LAN_DESK") != "1":
            raise LanConfigError("ORNG_LAN_DESK is not 1")
        host = assert_lan_bind(os.environ.get("ORNG_LAN_DESK_HOST", ""))
        port = int(os.environ.get("ORNG_LAN_DESK_PORT", "8443"))
        if port == 8000 or not (1 <= port <= 65535):
            raise LanConfigError("LAN desk port must not be 8000")
        names = [part.strip() for part in os.environ.get("ORNG_LAN_DESK_NAMES", "").split(",") if part.strip()]
        hosts = {host, *names}
        origins = {f"https://{name}:{port}" for name in hosts}
        if port == 443:
            origins.update(f"https://{name}" for name in hosts)
        cert = os.environ.get("ORNG_LAN_DESK_CERT", "").strip()
        key = os.environ.get("ORNG_LAN_DESK_KEY", "").strip()
        if not cert or not key:
            raise LanConfigError("LAN desk requires a TLS certificate and key")
        return cls(
            host=host,
            port=port,
            allowed_hosts=hosts,
            origins=origins,
            secret_path=config_root / "desk-operator.secret",
            certfile=cert,
            keyfile=key,
        )


def assert_lan_bind(host: str) -> str:
    """A single address. Not every interface, and not the Art-Net network."""
    text = host.strip()
    if text in {"", "0.0.0.0", "::", "*", "[::]"}:
        raise LanConfigError("LAN desk does not listen on every interface")
    try:
        address = ipaddress.ip_address(text)
    except ValueError as exc:
        raise LanConfigError("LAN desk host must be one IP address") from exc
    if isinstance(address, ipaddress.IPv4Address) and address in _ARTNET_NET:
        raise LanConfigError("LAN desk does not listen on the Art-Net network")
    if address.is_multicast or address.is_unspecified:
        raise LanConfigError("LAN desk does not listen on every interface")
    return text


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise LanConfigError("Operator password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    )


def write_operator_secret(path: Path, password: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(hash_password(password), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def password_matches(stored: str, password: str) -> bool:
    try:
        kind, n_s, r_s, p_s, salt_b64, hash_b64 = stored.strip().split("$")
        if kind != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=32,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, base64.b64decode(hash_b64))


def scoped_source(session_id: str, raw: object) -> str:
    """A LAN tab cannot name a cloud source or another session."""
    tab = "tab"
    if isinstance(raw, str) and raw.startswith("web:"):
        rest = raw[4:]
        if _TAB_RE.fullmatch(rest):
            tab = rest
    return f"lan:{session_id}:{tab}"


@dataclass
class _Session:
    public_id: str
    token_hash: str
    expires: float


@dataclass
class LanGate:
    config: LanConfig
    sessions: dict[str, _Session] = field(default_factory=dict)
    failures: dict[str, list[float]] = field(default_factory=dict)

    def request_host(self, request: Request) -> str:
        raw = request.headers.get("host", "")
        return raw.split(":")[0].strip().lower()

    def host_allowed(self, request: Request) -> bool:
        return self.request_host(request) in {item.lower() for item in self.config.allowed_hosts}

    def origin_ok(self, origin: str, host: str) -> bool:
        if not origin:
            return False
        allowed = {item.lower() for item in self.config.origins}
        if origin.lower() in allowed:
            return True
        if not self.config.allow_http:
            return False
        return origin.lower() in {f"http://{host}", f"http://{host}:{self.config.port}"}

    def origin_allowed(self, request: Request) -> bool:
        return self.origin_ok(request.headers.get("origin", ""), self.request_host(request))

    def session_from_cookie(self, cookie: str | None) -> _Session | None:
        if not cookie:
            return None
        now = time.time()
        digest = hashlib.sha256(cookie.encode("utf-8")).hexdigest()
        found = self.sessions.get(digest)
        if found is None or found.expires <= now:
            if found is not None:
                self.sessions.pop(digest, None)
            return None
        return found

    def websocket_denial(self, websocket: WebSocket) -> str | None:
        host = websocket.headers.get("host", "").split(":")[0].strip().lower()
        if host not in {item.lower() for item in self.config.allowed_hosts}:
            return "host"
        if not self.origin_ok(websocket.headers.get("origin", ""), host):
            return "origin"
        session = self.session_from_cookie(websocket.cookies.get(COOKIE))
        if session is None:
            return "auth"
        raw = websocket.query_params.get("source_id")
        websocket.state.lan_source_id = scoped_source(session.public_id, raw)
        return None

    def _locked(self, client: str, now: float) -> bool:
        recent = [stamp for stamp in self.failures.get(client, []) if now - stamp < FAIL_WINDOW_S]
        self.failures[client] = recent
        return len(recent) >= MAX_FAILURES

    def note_failure(self, client: str) -> None:
        self.failures.setdefault(client, []).append(time.time())

    def open_session(self, response: Response) -> _Session:
        token = secrets.token_urlsafe(32)
        public_id = secrets.token_hex(8)
        session = _Session(
            public_id=public_id,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expires=time.time() + SESSION_TTL_S,
        )
        self.sessions[session.token_hash] = session
        response.set_cookie(
            COOKIE,
            token,
            max_age=SESSION_TTL_S,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )
        return session


def _allowed(method: str, path: str) -> bool:
    if method == "GET" and path in _GET:
        return True
    if method == "GET" and path.startswith("/api/presets/"):
        rest = path.removeprefix("/api/presets/")
        return bool(rest) and "/" not in rest
    if method == "GET" and path in {"/api/scene-draft", "/api/scene-draft/presets"}:
        return True
    if method == "GET" and path.startswith("/api/scene-draft/presets/"):
        rest = path.removeprefix("/api/scene-draft/presets/")
        return bool(rest) and "/" not in rest
    if method == "GET" and path.startswith("/api/auto/preview-clip"):
        return True
    if method == "POST" and path in _POST:
        return True
    return False


def _public(method: str, path: str) -> bool:
    if path == "/api/lan/session" and method == "GET":
        return True
    if path == "/api/lan/login" and method == "POST":
        return True
    return method in {"GET", "HEAD"} and not path.startswith("/api")


def create_lan_desk_app(
    runtime: AppRuntime,
    config: LanConfig,
    *,
    frontend_dist: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="ORNG LED CONTROL", docs_url=None, redoc_url=None)
    app.state.runtime = runtime
    gate = LanGate(config)
    app.state.lan_gate = gate

    @app.middleware("http")
    async def lan_door(request: Request, call_next):  # type: ignore[no-untyped-def]
        if not gate.host_allowed(request):
            return JSONResponse({"detail": "Цей хост не обслуговує локальний пульт."}, status_code=421)
        path = request.url.path
        method = request.method.upper()
        if any(key in request.query_params for key in ("token", "access_token", "password")):
            return JSONResponse({"detail": "Секрет у адресі не приймається."}, status_code=400)
        if _public(method, path):
            if method == "POST":
                if not gate.origin_allowed(request):
                    return JSONResponse({"detail": "Запит з чужого сайту відхилено."}, status_code=403)
            return await call_next(request)
        if not _allowed(method, path):
            return JSONResponse({"detail": "Ця дія не входить у локальний пульт."}, status_code=404)
        if method not in {"GET", "HEAD"} and not gate.origin_allowed(request):
            return JSONResponse({"detail": "Запит з чужого сайту відхилено."}, status_code=403)
        session = gate.session_from_cookie(request.cookies.get(COOKIE))
        if session is None:
            return JSONResponse({"detail": "Потрібен локальний вхід."}, status_code=401)
        if method == "POST" and (path in _STAMP or path.startswith("/api/commands/")):
            await _stamp_json(request, session.public_id, always=path in _STAMP)
        return await call_next(request)

    @app.get("/api/lan/session")
    async def lan_session(request: Request) -> dict:
        session = gate.session_from_cookie(request.cookies.get(COOKIE))
        return {
            "lan_desk": True,
            "authenticated": session is not None,
            "role": "operator" if session is not None else None,
        }

    @app.post("/api/lan/login")
    async def lan_login(request: Request) -> JSONResponse:
        client = request.client.host if request.client else "unknown"
        if gate._locked(client, time.time()):
            return JSONResponse({"detail": "Забагато спроб. Зачекайте і спробуйте знову."}, status_code=429)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        password = body.get("password") if isinstance(body, dict) else None
        stored = ""
        try:
            stored = config.secret_path.read_text(encoding="utf-8")
        except OSError:
            stored = ""
        if not isinstance(password, str) or not stored or not password_matches(stored, password):
            gate.note_failure(client)
            return JSONResponse({"detail": "Невірний пароль."}, status_code=401)
        response = JSONResponse({"ok": True, "role": "operator"})
        gate.open_session(response)
        gate.failures.pop(client, None)
        return response

    @app.post("/api/lan/logout")
    async def lan_logout(request: Request) -> JSONResponse:
        cookie = request.cookies.get(COOKIE)
        if cookie:
            gate.sessions.pop(hashlib.sha256(cookie.encode("utf-8")).hexdigest(), None)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
        return response

    app.include_router(build_api_router())
    app.include_router(build_ws_router())
    if frontend_dist is not None:
        from orng_led.main import _register_frontend

        _register_frontend(app, frontend_dist)
    return app


async def _stamp_json(request: Request, session_id: str, *, always: bool) -> None:
    body = await request.body()
    if not body:
        return
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return
    if not always and "source_id" not in data:
        return
    data["source_id"] = scoped_source(session_id, data.get("source_id"))
    request._body = json.dumps(data).encode("utf-8")  # noqa: SLF001


class _LanServer:
    def __init__(self, server: object, task: asyncio.Task[None]) -> None:
        self.server = server
        self.task = task

    async def aclose(self) -> None:
        self.server.should_exit = True  # type: ignore[attr-defined]
        try:
            await asyncio.wait_for(self.task, timeout=5)
        except TimeoutError:
            self.task.cancel()


async def start_from_env(runtime: AppRuntime) -> _LanServer | None:
    """Start only when the venue explicitly enables the desk. A bad setting stays closed."""
    if os.environ.get("ORNG_LAN_DESK") != "1":
        return None
    try:
        config = LanConfig.from_env(runtime.config_root)
    except (LanConfigError, ValueError) as exc:
        logger.error("LAN desk stayed closed: %s", exc)
        return None
    import uvicorn

    from orng_led.main import resolve_frontend_dist

    app = create_lan_desk_app(runtime, config, frontend_dist=resolve_frontend_dist())
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.host,
            port=config.port,
            ssl_certfile=config.certfile,
            ssl_keyfile=config.keyfile,
            log_level="info",
        )
    )
    task = asyncio.create_task(server.serve(), name="orng-lan-desk")
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.02)
    if not server.started:
        server.should_exit = True
        task.cancel()
        logger.error("LAN desk did not open")
        return None
    logger.info("LAN desk listening on https://%s:%s", config.host, config.port)
    return _LanServer(server, task)


def set_password_cli() -> None:
    """Record the operator password. It is read from the terminal, not from the command line."""
    import getpass

    from orng_led.config.io import default_config_dir

    if not os.environ.get("ORNG_CONFIG_ROOT", "").strip():
        raise SystemExit("Set ORNG_CONFIG_ROOT to the venue data directory first")
    password = getpass.getpass("Operator password: ")
    again = getpass.getpass("Repeat: ")
    if password != again:
        raise SystemExit("Passwords differ")
    path = default_config_dir() / "desk-operator.secret"
    write_operator_secret(path, password)
    print(f"Recorded {path.name}. The password was not printed.")


if __name__ == "__main__":
    set_password_cli()
