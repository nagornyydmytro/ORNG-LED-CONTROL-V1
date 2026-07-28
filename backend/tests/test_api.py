"""API, WebSocket and runtime lifecycle tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.main import create_app


@pytest.fixture
def runtime() -> AppRuntime:
    return AppRuntime.create(autostart_loop=False)


@pytest.fixture
def client(runtime: AppRuntime):
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as test_client:
        yield test_client, runtime


def test_health_and_ready(client) -> None:
    test_client, _runtime = client
    health = test_client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["transport"] == "mock"
    assert health["output_armed"] is False
    assert health["artnet_network_enabled"] is False
    assert health["ready"] is True

    ready = test_client.get("/api/ready").json()
    assert ready["ready"] is True
    assert ready["engine"] is True


def test_initial_state_contains_engine_output_and_frame(client) -> None:
    test_client, runtime = client
    state = test_client.get("/api/state").json()
    assert state["engine"]["preset_id"] == "P05"
    assert state["engine"]["blackout"] is True
    assert state["frame"] == [0] * DMX_UNIVERSE_SIZE
    assert state["output"]["transport"] == "mock"
    assert state["output"]["armed"] is False
    assert len(state["frame"]) == DMX_UNIVERSE_SIZE
    assert "P05" in state["presets"]
    assert len(state["fixture_ids"]) == 12
    assert state["preview_speed"] == 1.0
    assert len(state["simulator"]["pars"]) == 4
    assert len(state["simulator"]["bars"]) == 4
    assert len(state["simulator"]["beams"]) == 2
    assert len(state["simulator"]["faces"]) == 2
    assert runtime.engine is test_client.app.state.runtime.engine
    assert runtime.engine.overlays.blackout is True


def test_preview_speed_command(client) -> None:
    test_client, runtime = client
    ack = test_client.post(
        "/api/commands/preview-speed",
        json={"value": 30},
    ).json()
    assert ack["ok"] is True
    assert ack["state"]["preview_speed"] == 30.0
    assert runtime.preview_speed == 30.0


def test_config_and_presets_endpoints(client) -> None:
    test_client, _runtime = client
    app_cfg = test_client.get("/api/config/app").json()
    assert app_cfg["transport"] == "mock"
    assert app_cfg["output_armed"] is False

    patch = test_client.get("/api/config/patch").json()
    assert len(patch["fixtures"]) == 12

    profiles = test_client.get("/api/config/profiles").json()
    assert "par_7ch_provisional" in profiles
    assert profiles["par_7ch_provisional"]["hardware_verified"] is False

    layout = test_client.get("/api/config/layout").json()
    assert layout["viewer_facing"] is True

    presets = test_client.get("/api/presets").json()["presets"]
    assert any(item["id"] == "P05" for item in presets)


def test_commands_update_state(client) -> None:
    test_client, runtime = client
    ack = test_client.post(
        "/api/commands/select-preset",
        json={"preset_id": "P05", "reset_clock": True},
    ).json()
    assert ack["ok"] is True
    assert ack["state"]["engine"]["preset_id"] == "P05"

    face = test_client.post(
        "/api/commands/face",
        json={"enabled": True, "brightness": 0.8},
    ).json()
    assert face["state"]["engine"]["face_on"] is True
    assert face["state"]["engine"]["face_brightness"] == 0.8

    hit = test_client.post("/api/commands/white-hit", json={}).json()
    assert hit["state"]["engine"]["white_hit_active"] is True

    strobe = test_client.post("/api/commands/strobe", json={"action": "press"}).json()
    assert strobe["state"]["engine"]["strobe_held"] is True
    assert runtime.engine.overlays.strobe_held is True

    blackout = test_client.post("/api/commands/blackout", json={"enabled": True}).json()
    assert blackout["state"]["engine"]["blackout"] is True
    assert blackout["state"]["frame"] == [0] * DMX_UNIVERSE_SIZE

    bright = test_client.post(
        "/api/commands/master-brightness",
        json={"value": 0.4},
    ).json()
    assert bright["state"]["engine"]["master_brightness"] == 0.4


def test_command_idempotency(client) -> None:
    test_client, runtime = client
    first = test_client.post(
        "/api/commands/white-hit",
        json={"client_command_id": "cmd-1"},
    ).json()
    until = runtime.engine.overlays.white_hit_until
    second = test_client.post(
        "/api/commands/white-hit",
        json={"client_command_id": "cmd-1"},
    ).json()
    assert second["idempotent_replay"] is True
    assert runtime.engine.overlays.white_hit_until == until
    assert first["state"]["sequence"] == second["state"]["sequence"]


def test_unknown_preset_returns_404(client) -> None:
    test_client, _runtime = client
    response = test_client.post(
        "/api/commands/select-preset",
        json={"preset_id": "P99"},
    )
    assert response.status_code == 404


def test_websocket_hello_and_command_broadcast(client) -> None:
    test_client, runtime = client
    with test_client.websocket_connect("/api/ws") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert hello["state"]["engine"]["preset_id"] == "P05"

        test_client.post("/api/commands/blackout", json={"enabled": True})
        # Manual tick/broadcast already happens in command handler.
        message = ws.receive_json()
        assert message["type"] == "state"
        assert message["state"]["engine"]["blackout"] is True
        assert id(runtime.engine) == id(test_client.app.state.runtime.engine)


def test_websocket_reconnect_does_not_duplicate_engine(client) -> None:
    test_client, runtime = client
    engine_id = id(runtime.engine)

    with test_client.websocket_connect("/api/ws") as ws1:
        hello1 = ws1.receive_json()
        assert hello1["type"] == "hello"
        test_client.post("/api/commands/strobe", json={"action": "press"})
        # drain broadcast
        ws1.receive_json()
        assert runtime.engine.overlays.strobe_held is True

    # Disconnect releases held strobe but keeps the same engine instance.
    assert runtime.engine.overlays.strobe_held is False
    assert id(runtime.engine) == engine_id

    with test_client.websocket_connect("/api/ws") as ws2:
        hello2 = ws2.receive_json()
        assert hello2["type"] == "hello"
        assert id(test_client.app.state.runtime.engine) == engine_id


def test_websocket_focus_and_visibility_failsafe(client) -> None:
    test_client, runtime = client
    runtime.engine.strobe_press()
    with test_client.websocket_connect("/api/ws") as ws:
        ws.receive_json()  # hello
        ws.send_json({"type": "focus_loss"})
        update = ws.receive_json()
        assert update["state"]["engine"]["strobe_held"] is False

    runtime.engine.strobe_press()
    with test_client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        ws.send_json({"type": "visibility_hidden"})
        update = ws.receive_json()
        assert update["state"]["engine"]["strobe_held"] is False


def test_shutdown_invokes_output_safety(client) -> None:
    test_client, runtime = client
    runtime.engine.strobe_press()
    runtime.tick(dt_s=0.1)
    assert runtime.output.frames_sent >= 1

    ack = test_client.post("/api/shutdown").json()
    assert ack["ok"] is True
    assert runtime.engine.overlays.blackout is True
    assert runtime.engine.overlays.strobe_held is False
    assert runtime.output.armed is False
    assert runtime.output.transport_kind.value == "mock"
    assert runtime.is_ready is False


def test_module_app_exports_factory_app() -> None:
    from orng_led import main as main_module

    assert main_module.app.title == "ORNG LED CONTROL"
