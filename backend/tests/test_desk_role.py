"""Desktop execution choice: Raspberry commands do not move the local engine."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.config import default_scene_dir
from orng_led.desktop.desk_role import desk_role, saved_desk_role, set_desk_role
from orng_led.main import create_app
from orng_led.output.artnet_discover import art_poll_packet, parse_poll_reply


@pytest.fixture
def isolated_config(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(default_scene_dir(), target)
    return target


def test_live_effect_press_and_release_share_one_hold_id() -> None:
    from orng_led.desktop.remote_desk import command_for_request

    press = command_for_request(
        "/api/input/action",
        {
            "action": "sweep_press",
            "edge": "press",
            "source_id": "web:1",
            "client_command_id": "press-id",
        },
    )
    release = command_for_request(
        "/api/input/action",
        {
            "action": "sweep_release",
            "edge": "release",
            "source_id": "web:1",
            "client_command_id": "release-id",
        },
    )
    assert press is not None and release is not None
    assert press[0] == "sweep_press"
    assert release[0] == "sweep_release"
    assert press[1]["hold_id"] == release[1]["hold_id"]
    assert press[1]["hold_id"] != "press-id"
    assert "release-id" not in str(release[1]["hold_id"])


def test_poll_reply_parser() -> None:
    packet = art_poll_packet()
    assert packet.startswith(b"Art-Net\x00")
    reply = b"Art-Net\x00" + (0x2100).to_bytes(2, "little") + bytes((10, 1, 2, 3))
    assert parse_poll_reply(reply) == "10.1.2.3"
    assert parse_poll_reply(packet) is None


def test_saved_choice_survives_raspberry_env(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORNG_DESKTOP_OUTPUT", "raspberry")
    set_desk_role(isolated_config, "local")
    assert desk_role(isolated_config) == "local"
    assert saved_desk_role(isolated_config) == "local"


def test_raspberry_blackout_does_not_change_local_engine(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    set_desk_role(runtime.config_root, "raspberry")
    seen: list[str] = []

    async def _forward(command_type: str, payload: dict) -> dict:
        seen.append(command_type)
        return {"ok": True, "status": "applied", "command_id": payload.get("command_id")}

    monkeypatch.setattr(
        "orng_led.desktop.remote_desk.forward_operator_command",
        _forward,
    )
    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app) as client:
        before = runtime.engine.overlays.blackout
        response = client.post("/api/commands/blackout", json={"enabled": not before})
    assert response.status_code == 200
    assert seen == ["blackout"]
    assert runtime.engine.overlays.blackout is before
    assert runtime.output.armed is False
    assert saved_desk_role(runtime.config_root) == "raspberry"


def test_executor_outage_keeps_raspberry_role(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orng_led.desktop.remote_desk import RemoteDeskError

    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    set_desk_role(runtime.config_root, "raspberry")

    async def _forward(command_type: str, payload: dict) -> dict:
        raise RemoteDeskError(503, "Виконавець недоступний. Режим не змінено.")

    monkeypatch.setattr(
        "orng_led.desktop.remote_desk.forward_operator_command",
        _forward,
    )
    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app) as client:
        response = client.post("/api/commands/blackout", json={"enabled": False})
    assert response.status_code == 503
    assert saved_desk_role(runtime.config_root) == "raspberry"
    assert runtime.output.armed is False


def test_executor_state_is_the_remote_show_not_the_local_engine(
    isolated_config: Path,
) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    set_desk_role(runtime.config_root, "raspberry")
    runtime.engine.overlays.blackout = False
    runtime.executor_online = True
    runtime.executor_view = {
        "installation_id": "pi-1",
        "blackout": True,
        "preset_id": "P04",
        "episode_index": 3,
        "face_on": True,
        "face_brightness": 0.4,
        "strobe_speed": 0.2,
        "sweep_speed": 0.3,
        "preview_speed": 1.5,
        "current_scene_id": "orng-bar",
        "scene_name": "ORNG BAR",
        "bpm": {"bpm": 120, "confidence": 0.8},
        "operator_commands": ["blackout", "select_preset"],
        "pad_presets": ["P01"] * 9,
        "stage": {
            "pars": [
                {
                    "id": "par_1",
                    "label": "PAR",
                    "kind": "par",
                    "side": "l",
                    "ring": "rear",
                    "face": False,
                    "order": 1,
                    "r": 1.0,
                    "g": 0.4,
                    "b": 0.0,
                    "w": 0.0,
                    "intensity": 0.8,
                }
            ],
            "bars": [],
            "beams": [],
            "faces": [],
            "nonzero_channels": 4,
            "blackout_visual": False,
        },
    }
    state = runtime.build_state()
    assert state.desk_role == "raspberry"
    assert state.executor_online is True
    assert state.executor_installation_id == "pi-1"
    assert state.engine.blackout is True
    assert state.engine.preset_id == "P04"
    assert state.engine.episode_index == 3
    assert state.engine.face_on is True
    assert state.engine.face_brightness == 0.4
    assert state.scene_id == "orng-bar"
    assert state.scene_name == "ORNG BAR"
    assert state.bpm == {"bpm": 120, "confidence": 0.8}
    assert state.capabilities == ["blackout", "select_preset"]
    assert state.output.armed is False
    assert state.output.transport == "mock"
    assert state.simulator.pars[0].id == "par_1"
    assert state.simulator.pars[0].intensity == pytest.approx(0.8)
    runtime.executor_online = False
    kept = runtime.build_state()
    assert kept.engine.preset_id == "P04"
    assert kept.engine.blackout is True
    assert kept.executor_online is False
    assert kept.output.last_error
    assert "Локальний вивід" in kept.output.last_error
    assert kept.simulator.pars == []
    assert kept.simulator.blackout_visual is True


def test_accepted_is_not_a_finished_apply(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    set_desk_role(runtime.config_root, "raspberry")

    async def _forward(command_type: str, payload: dict) -> dict:
        return {"ok": True, "status": "accepted", "command_id": "c-1"}

    monkeypatch.setattr("orng_led.desktop.remote_desk.forward_operator_command", _forward)
    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app) as client:
        response = client.post("/api/commands/blackout", json={"enabled": True})
    assert response.status_code == 504
    body = response.json()
    assert body["accepted"] is False
    assert body["command_status"] == "accepted"
    assert body["command_id"] == "c-1"


def test_a_new_press_gets_a_new_hold_id() -> None:
    from orng_led.desktop.remote_desk import command_for_request

    first = command_for_request(
        "/api/input/action",
        {"action": "strobe_press", "source_id": "win-a"},
    )
    second = command_for_request(
        "/api/input/action",
        {"action": "strobe_press", "source_id": "win-a"},
    )
    old_release = command_for_request(
        "/api/input/action",
        {"action": "strobe_release", "source_id": "win-a", "hold_id": first[1]["hold_id"]},
    )
    assert first is not None and second is not None and old_release is not None
    assert first[1]["hold_id"] != second[1]["hold_id"]
    assert old_release[1]["hold_id"] == first[1]["hold_id"]


def test_feature_flags_do_not_allow_every_baseline_command() -> None:
    from orng_led.desktop.remote_desk import executor_supports, settle_command_status

    view = {"capabilities": ["scene_sync"]}
    assert executor_supports("blackout", view) is True
    assert executor_supports("seek_episode", view) is False
    assert executor_supports("bpm_sync", view) is False
    named = {"operator_commands": ["blackout"]}
    assert executor_supports("select_preset", named) is False
    assert executor_supports("bpm_sync", {"operator_commands": ["bpm_sync"]}) is True
    assert settle_command_status([{"command_id": "c", "status": "accepted"}], "c") == "unknown"
    assert settle_command_status([{"command_id": "c", "status": "applied"}], "c") == "applied"
    assert settle_command_status([{"command_id": "c", "status": "failed"}], "c") == "failed"


def test_raspberry_device_state_lists_auto_director(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_MODE", "raspberry")
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    caps = runtime.build_state().capabilities or []
    assert "auto_director" in caps
    assert "bpm_sync" in caps


def test_unknown_executor_rejects_seek(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    set_desk_role(runtime.config_root, "raspberry")
    seen: list[str] = []

    async def _forward(command_type: str, payload: dict) -> dict:
        seen.append(command_type)
        return {"ok": True, "status": "applied", "command_id": payload.get("command_id")}

    monkeypatch.setattr("orng_led.desktop.remote_desk.forward_operator_command", _forward)
    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app) as client:
        response = client.post("/api/commands/seek-episode", json={"episode_index": 1})
    assert response.status_code == 409
    assert seen == []
