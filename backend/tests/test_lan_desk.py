"""Venue LAN desk on a Mock engine. No Art-Net socket and no venue process."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from orng_led.api.runtime import AppRuntime
from orng_led.desk.lan import (
    LanConfig,
    LanConfigError,
    assert_lan_bind,
    create_lan_desk_app,
    start_from_env,
    write_operator_secret,
)
from orng_led.input.contract import InputAction, InputEvent, InputSource
from orng_led.main import FRONTEND_DIST, create_app, frontend_dist_for

PASSWORD = "operator-pass"
ORIGIN = {"origin": "http://testserver"}


def _config(tmp_path: Path) -> LanConfig:
    secret = tmp_path / "desk-operator.secret"
    write_operator_secret(secret, PASSWORD)
    return LanConfig(
        host="127.0.0.1",
        port=8443,
        allowed_hosts={"testserver", "127.0.0.1"},
        origins={"https://127.0.0.1:8443"},
        secret_path=secret,
        certfile="",
        keyfile="",
        allow_http=True,
    )


@pytest.fixture
def runtime() -> AppRuntime:
    active = AppRuntime.create(autostart_loop=False)
    yield active
    asyncio.run(active.shutdown())


def _login(client: TestClient) -> str:
    response = client.post("/api/lan/login", json={"password": PASSWORD}, headers=ORIGIN)
    assert response.status_code == 200
    assert response.json()["role"] == "operator"
    header = response.headers["set-cookie"]
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "samesite=strict" in header.lower()
    token = header.split("orng_lan=", 1)[1].split(";", 1)[0]
    assert token not in response.text
    assert PASSWORD not in header
    client.cookies.set("orng_lan", token)
    return token


def test_bind_policy_refuses_every_interface_and_the_artnet_network() -> None:
    with pytest.raises(LanConfigError):
        assert_lan_bind("0.0.0.0")
    with pytest.raises(LanConfigError):
        assert_lan_bind("::")
    with pytest.raises(LanConfigError):
        assert_lan_bind("2.0.0.10")
    assert assert_lan_bind("127.0.0.1") == "127.0.0.1"


def test_disabled_env_does_not_open_a_socket(runtime: AppRuntime, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORNG_LAN_DESK", raising=False)
    assert asyncio.run(start_from_env(runtime)) is None
    monkeypatch.setenv("ORNG_LAN_DESK", "1")
    monkeypatch.setenv("ORNG_LAN_DESK_HOST", "0.0.0.0")
    monkeypatch.setenv("ORNG_LAN_DESK_CERT", "missing.pem")
    monkeypatch.setenv("ORNG_LAN_DESK_KEY", "missing.key")
    assert asyncio.run(start_from_env(runtime)) is None


def test_loopback_api_stays_open_without_the_lan_gate(runtime: AppRuntime) -> None:
    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/lan/session").status_code == 404


def test_desk_without_wan_authenticates_commands_and_keeps_one_output(
    runtime: AppRuntime, tmp_path: Path
) -> None:
    page = tmp_path / "frontend"
    (page / "assets").mkdir(parents=True)
    (page / "index.html").write_text("<!doctype html><title>desk</title>", encoding="utf-8")
    (page / "assets" / "app.js").write_text("/* local */", encoding="utf-8")
    app = create_lan_desk_app(runtime, _config(tmp_path), frontend_dist=page)
    transport_before = runtime.output.transport_kind.value
    with TestClient(app) as client:
        opened = client.get("/")
        assert opened.status_code == 200
        assert "desk" in opened.text
        assert client.get("/assets/app.js").status_code == 200
        session = client.get("/api/lan/session")
        assert session.status_code == 200
        assert session.json()["authenticated"] is False
        assert client.get("/api/state").status_code == 401
        assert client.post("/api/lan/login", json={"password": "nope"}, headers=ORIGIN).status_code == 401
        leaked = client.get("/api/lan/login", params={"password": PASSWORD})
        assert leaked.status_code == 400

        token = _login(client)
        state = client.get("/api/state")
        assert state.status_code == 200
        fresh = TestClient(app)
        assert fresh.get("/api/state").status_code == 401
        fresh.cookies.set("orng_lan", token)
        assert fresh.get("/api/state").status_code == 200

        foreign = client.post(
            "/api/commands/blackout",
            json={"enabled": True},
            headers={"origin": "https://light.orng.wtf"},
        )
        assert foreign.status_code == 403
        wrong_host = client.post(
            "/api/commands/blackout",
            json={"enabled": True},
            headers={**ORIGIN, "host": "evil.example"},
        )
        assert wrong_host.status_code == 421

        for path in (
            "/api/desk/role",
            "/api/output/activate-artnet",
            "/api/output/arm",
            "/api/config/app",
            "/api/cloud/pair/start",
            "/api/app/update/apply",
            "/api/shutdown",
            "/api/setup/reload",
        ):
            blocked = client.post(path, json={}, headers=ORIGIN)
            assert blocked.status_code == 404, path
        listed = client.get("/api/scene-draft/presets")
        assert listed.status_code == 200
        assert client.post("/api/scene-draft/presets", json={}, headers=ORIGIN).status_code == 404
        head = client.post(
            "/api/commands/head-desk",
            json={"enabled": False, "light": False, "scene": True, "color": "red", "x": 0, "y": 0.5},
            headers=ORIGIN,
        )
        assert head.status_code == 200, head.text
        assert client.post("/api/setup/reload", json={}, headers=ORIGIN).status_code == 404

        for index in range(40):
            ack = client.post(
                "/api/commands/blackout",
                json={"enabled": index % 2 == 0, "client_command_id": f"b-{index}"},
                headers=ORIGIN,
            )
            assert ack.status_code == 200
            body = ack.json()
            assert body["ok"] is True
            assert body.get("command_status") != "queued"
            assert body["idempotent_replay"] is False
            assert body["state"]["engine"]["blackout"] is (index % 2 == 0)
        replay = client.post(
            "/api/commands/blackout",
            json={"enabled": False, "client_command_id": "b-1"},
            headers=ORIGIN,
        )
        assert replay.status_code == 200
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["ok"] is True

        runtime.input_dispatcher.dispatch(
            InputEvent(
                action=InputAction.STROBE_PRESS,
                source=InputSource.UI,
                edge="press",
                source_id="cloud:web:old",
            )
        )
        held = client.post(
            "/api/input/action",
            json={"action": "strobe_press", "edge": "press", "source": "ui", "source_id": "web:tab-a"},
            headers=ORIGIN,
        )
        assert held.status_code == 200
        other = client.post(
            "/api/input/action",
            json={"action": "strobe_press", "edge": "press", "source": "ui", "source_id": "web:tab-b"},
            headers=ORIGIN,
        )
        assert other.status_code == 200
        holders = runtime.input_dispatcher.holds
        assert holders.is_held_by("strobe", "cloud:web:old")
        lan_holders = [item for item in holders._holders["strobe"] if item.startswith("lan:")]  # noqa: SLF001
        assert len(lan_holders) == 2

        with client.websocket_connect(
            "/api/ws?source_id=web:tab-a",
            headers={**ORIGIN, "cookie": f"orng_lan={token}"},
        ) as socket:
            hello = socket.receive_json()
            assert hello["type"] == "hello"
            assert hello["source_id"].startswith("lan:")
            assert "cloud:web" not in hello["source_id"]
        assert holders.is_held_by("strobe", "cloud:web:old")
        assert not any(item.endswith(":tab-a") for item in holders._holders.get("strobe", set()))  # noqa: SLF001
        assert any(item.endswith(":tab-b") for item in holders._holders["strobe"])  # noqa: SLF001

        bare = TestClient(app)
        with pytest.raises(WebSocketDisconnect):
            with bare.websocket_connect("/api/ws?source_id=web:tab-b", headers=ORIGIN) as denied:
                denied.receive_json()

        again = client.post(
            "/api/commands/blackout",
            json={"enabled": True, "client_command_id": "after-drop"},
            headers=ORIGIN,
        )
        assert again.status_code == 200
        assert again.json()["state"]["engine"]["blackout"] is True
        assert runtime.output.transport_kind.value == transport_before == "mock"
        assert runtime.output.udp_active is False
        assert runtime.output.armed is False


def test_listener_speaks_https_on_one_address(runtime: AppRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import ipaddress
    import socket
    import ssl
    import urllib.request
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "desk.pem"
    key_path = tmp_path / "desk.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    write_operator_secret(runtime.config_root / "desk-operator.secret", PASSWORD)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    monkeypatch.setenv("ORNG_LAN_DESK", "1")
    monkeypatch.setenv("ORNG_LAN_DESK_HOST", "127.0.0.1")
    monkeypatch.setenv("ORNG_LAN_DESK_PORT", str(port))
    monkeypatch.setenv("ORNG_LAN_DESK_CERT", str(cert_path))
    monkeypatch.setenv("ORNG_LAN_DESK_KEY", str(key_path))

    async def session() -> None:
        server = await start_from_env(runtime)
        assert server is not None
        try:
            def fetch() -> bytes:
                context = ssl.create_default_context()
                context.load_verify_locations(cert_path)
                with urllib.request.urlopen(  # noqa: S310
                    f"https://127.0.0.1:{port}/api/lan/session",
                    context=context,
                    timeout=5,
                ) as response:
                    assert response.status == 200
                    return response.read()

            body = await asyncio.to_thread(fetch)
            assert b'"authenticated": false' in body or b'"authenticated":false' in body
        finally:
            await server.aclose()

    asyncio.run(session())


def _rank(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def test_frontend_directory_follows_the_package_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packaged = tmp_path / "release" / "orng_led"
    packaged.mkdir(parents=True)
    page = tmp_path / "release" / "frontend_dist"
    page.mkdir()
    (page / "index.html").write_text("release", encoding="utf-8")
    decoy = tmp_path / "elsewhere" / "frontend_dist"
    decoy.mkdir(parents=True)
    (decoy / "index.html").write_text("decoy", encoding="utf-8")
    monkeypatch.chdir(decoy.parent)
    monkeypatch.delenv("ORNG_FRONTEND_DIST", raising=False)
    assert frontend_dist_for(packaged) == page

    checkout_pkg = tmp_path / "checkout" / "backend" / "orng_led"
    checkout_pkg.mkdir(parents=True)
    built = tmp_path / "checkout" / "frontend" / "dist"
    built.mkdir(parents=True)
    assert frontend_dist_for(checkout_pkg) == built

    lost = tmp_path / "lost" / "orng_led"
    lost.mkdir(parents=True)
    assert frontend_dist_for(lost) == lost.parent / "frontend_dist"
    assert not frontend_dist_for(lost).exists()

    monkeypatch.setenv("ORNG_FRONTEND_DIST", "named-dist")
    assert frontend_dist_for(packaged) == tmp_path / "release" / "named-dist"
    missing = tmp_path / "missing-release"
    monkeypatch.setenv("ORNG_FRONTEND_DIST", str(missing))
    assert frontend_dist_for(packaged) == missing


def _loopback_material(tmp_path: Path) -> tuple[Path, Path]:
    import ipaddress
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "desk.pem"
    key_path = tmp_path / "desk.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _enable_loopback(monkeypatch: pytest.MonkeyPatch, port: int, cert_path: Path, key_path: Path) -> None:
    monkeypatch.setenv("ORNG_LAN_DESK", "1")
    monkeypatch.setenv("ORNG_LAN_DESK_HOST", "127.0.0.1")
    monkeypatch.setenv("ORNG_LAN_DESK_PORT", str(port))
    monkeypatch.setenv("ORNG_LAN_DESK_CERT", str(cert_path))
    monkeypatch.setenv("ORNG_LAN_DESK_KEY", str(key_path))


def test_missing_release_stays_closed_even_if_cwd_has_a_page(
    runtime: AppRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    cert_path, key_path = _loopback_material(tmp_path)
    decoy = tmp_path / "cwd" / "frontend_dist"
    decoy.mkdir(parents=True)
    (decoy / "index.html").write_text("DECOY", encoding="utf-8")
    monkeypatch.chdir(decoy.parent)
    monkeypatch.setenv("ORNG_FRONTEND_DIST", str(tmp_path / "missing-release"))
    write_operator_secret(runtime.config_root / "desk-operator.secret", PASSWORD)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    _enable_loopback(monkeypatch, port, cert_path, key_path)

    async def session() -> None:
        server = await start_from_env(runtime)
        assert server is not None
        try:
            def fetch(path: str) -> httpx.Response:
                return httpx.get(
                    f"https://127.0.0.1:{port}{path}",
                    verify=str(cert_path),
                    trust_env=False,
                    timeout=5,
                )

            page = await asyncio.to_thread(fetch, "/")
            session_body = await asyncio.to_thread(fetch, "/api/lan/session")
            assert page.status_code == 404
            assert b"DECOY" not in page.content
            assert session_body.status_code == 200
            assert session_body.json()["authenticated"] is False
        finally:
            await server.aclose()

    asyncio.run(session())


@pytest.mark.skipif(not (FRONTEND_DIST / "index.html").is_file(), reason="frontend/dist missing")
def test_lifespan_serves_the_built_desk_and_acks_on_loopback(
    runtime: AppRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    cert_path, key_path = _loopback_material(tmp_path)
    decoy = tmp_path / "cwd" / "frontend_dist"
    decoy.mkdir(parents=True)
    (decoy / "index.html").write_text("DECOY", encoding="utf-8")
    monkeypatch.chdir(decoy.parent)
    monkeypatch.delenv("ORNG_FRONTEND_DIST", raising=False)
    write_operator_secret(runtime.config_root / "desk-operator.secret", PASSWORD)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    _enable_loopback(monkeypatch, port, cert_path, key_path)
    origin = f"https://127.0.0.1:{port}"
    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app):
        with httpx.Client(
            base_url=origin,
            verify=str(cert_path),
            trust_env=False,
            timeout=5,
            headers={"origin": origin},
        ) as client:
            page = client.get("/")
            assert page.status_code == 200
            assert "DECOY" not in page.text
            assert "/assets/" in page.text
            scripts = re.findall(r'src="(/assets/[^"]+\.js)"', page.text)
            styles = re.findall(r'href="(/assets/[^"]+\.css)"', page.text)
            assert scripts and styles
            for asset in scripts + styles:
                fetched = client.get(asset)
                assert fetched.status_code == 200, asset
                kind = fetched.headers.get("content-type", "")
                assert "text/html" not in kind
                assert b"<html" not in fetched.content[:200].lower()
            opened = client.get("/api/lan/session")
            assert opened.status_code == 200
            assert opened.json()["authenticated"] is False
            denied = client.get("/api/state")
            assert denied.status_code == 401
            login = client.post("/api/lan/login", json={"password": PASSWORD})
            assert login.status_code == 200
            cookie = login.headers["set-cookie"]
            assert "httponly" in cookie.lower()
            assert "secure" in cookie.lower()
            assert "samesite=strict" in cookie.lower()
            assert client.get("/api/state").status_code == 200
            foreign = client.post(
                "/api/commands/blackout",
                json={"enabled": True},
                headers={"origin": "https://light.orng.wtf"},
            )
            assert foreign.status_code == 403
            blocked = client.post("/api/output/activate-artnet", json={})
            assert blocked.status_code == 404
            samples: list[float] = []
            for index in range(40):
                started = time.perf_counter()
                ack = client.post(
                    "/api/commands/blackout",
                    json={"enabled": index % 2 == 0, "client_command_id": f"loop-{index}"},
                )
                samples.append(time.perf_counter() - started)
                assert ack.status_code == 200
                body = ack.json()
                assert body["ok"] is True
                assert body["idempotent_replay"] is False
                assert body["state"]["engine"]["blackout"] is (index % 2 == 0)
            replay = client.post(
                "/api/commands/blackout",
                json={"enabled": True, "client_command_id": "loop-0"},
            )
            assert replay.status_code == 200
            assert replay.json()["idempotent_replay"] is True
            assert replay.json()["state"]["engine"]["blackout"] is False
            p50 = _rank(samples, 0.50)
            p95 = _rank(samples, 0.95)
            slowest = max(samples)
            print(
                f"loopback ACK n={len(samples)} "
                f"p50={p50 * 1000:.2f}ms p95={p95 * 1000:.2f}ms max={slowest * 1000:.2f}ms"
            )
            assert p95 <= 0.100
            token = client.cookies.get("orng_lan")
            assert token
            asyncio.run(_two_tabs(runtime, origin, cert_path, token, client))
        assert runtime.output.transport_kind.value == "mock"
        assert runtime.output.armed is False
        assert runtime.output.udp_active is False


async def _two_tabs(
    runtime: AppRuntime,
    origin: str,
    cert_path: Path,
    token: str,
    client: httpx.Client,
) -> None:
    import ssl

    from websockets.asyncio.client import connect

    context = ssl.create_default_context()
    context.load_verify_locations(cert_path)

    async def hello(source: str):
        sock = await connect(
            f"{origin.replace('https', 'wss', 1)}/api/ws?source_id={source}",
            ssl=context,
            origin=origin,
            additional_headers={"Cookie": f"orng_lan={token}"},
            proxy=None,
        )
        payload = json.loads(await asyncio.wait_for(sock.recv(), 5))
        assert payload["type"] == "hello"
        return sock, str(payload["source_id"])

    first, first_id = await hello("web:tab-a")
    second, second_id = await hello("web:tab-b")
    try:
        assert first_id.endswith(":tab-a") and first_id.startswith("lan:")
        assert second_id.endswith(":tab-b") and "cloud:" not in second_id
        pressed = client.post(
            "/api/input/action",
            json={
                "action": "strobe_press",
                "edge": "press",
                "source": "ui",
                "source_id": "web:tab-a",
                "client_command_id": "press-a",
            },
        )
        assert pressed.status_code == 200
        other = client.post(
            "/api/input/action",
            json={
                "action": "strobe_press",
                "edge": "press",
                "source": "ui",
                "source_id": "web:tab-b",
                "client_command_id": "press-b",
            },
        )
        assert other.status_code == 200
        holders = runtime.input_dispatcher.holds._holders["strobe"]  # noqa: SLF001
        assert first_id in holders and second_id in holders
        await first.close()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and first_id in holders:
            await asyncio.sleep(0.02)
        assert first_id not in holders
        assert second_id in holders
        assert runtime.engine.overlays.strobe_held is True
        late = client.post(
            "/api/input/action",
            json={
                "action": "strobe_release",
                "edge": "release",
                "source": "ui",
                "source_id": "web:tab-a",
                "client_command_id": "release-a",
            },
        )
        assert late.status_code == 200
        assert second_id in holders
        assert runtime.engine.overlays.strobe_held is True
        again, again_id = await hello("web:tab-a")
        try:
            assert again_id == first_id
            replayed = client.post(
                "/api/input/action",
                json={
                    "action": "strobe_press",
                    "edge": "press",
                    "source": "ui",
                    "source_id": "web:tab-a",
                    "client_command_id": "press-a",
                },
            )
            assert replayed.status_code == 200
            assert replayed.json()["idempotent_replay"] is True
            assert again_id not in holders
            assert runtime.engine.overlays.strobe_held is True
        finally:
            await again.close()
        await second.close()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and (
            second_id in holders or runtime.engine.overlays.strobe_held
        ):
            await asyncio.sleep(0.02)
        assert second_id not in holders
        assert runtime.engine.overlays.strobe_held is False
        old = client.post(
            "/api/input/action",
            json={
                "action": "strobe_press",
                "edge": "press",
                "source": "ui",
                "source_id": "web:tab-a",
                "client_command_id": "press-a",
            },
        )
        assert old.status_code == 200
        assert old.json()["idempotent_replay"] is True
        assert runtime.engine.overlays.strobe_held is False
        old_other = client.post(
            "/api/input/action",
            json={
                "action": "strobe_press",
                "edge": "press",
                "source": "ui",
                "source_id": "web:tab-b",
                "client_command_id": "press-b",
            },
        )
        assert old_other.status_code == 200
        assert old_other.json()["idempotent_replay"] is True
        assert runtime.engine.overlays.strobe_held is False
    finally:
        for sock in (first, second):
            try:
                await sock.close()
            except Exception:  # noqa: BLE001
                pass

