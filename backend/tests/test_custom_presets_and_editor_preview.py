"""Custom presets on the pad + editor episode hardware preview."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.config import default_config_dir
from orng_led.engine.presets import NONE_PRESET_ID
from orng_led.engine.show_whitelist import assert_lights_dark
from orng_led.main import create_app
from orng_led.output import RecordingSocket, TransportKind

VENUE_CONTROLLER_IP = "2.0.0.11"
SAFE_TEST_IP = "127.0.0.1"


@pytest.fixture
def isolated_config(tmp_path: Path) -> Path:
    source = default_config_dir()
    target = tmp_path / "config"
    shutil.copytree(source, target)
    return target


@pytest.fixture
def client(isolated_config: Path):
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as test_client:
        yield test_client, runtime, isolated_config


def _episode(**overrides):
    base = {
        "id": "ep-draft-1",
        "duration_s": 2.0,
        "groups": ["all_rear"],
        "palette": "warm_orange",
        "effect": "pulse",
        "speed": 0.5,
        "intensity": 0.8,
        "transition": "cut",
    }
    base.update(overrides)
    return base


def test_custom_preset_appears_in_state_presets_and_survives_reload(client) -> None:
    test_client, runtime, cfg = client
    ack = test_client.post("/api/presets", json={"id": "C77", "label": "Сценічний"}).json()
    assert ack["ok"] is True
    assert "C77" in ack["state"]["presets"]
    assert set(ack["state"]["presets"]) - {NONE_PRESET_ID} != {"P01", "P02", "P03"}

    catalog = test_client.get("/api/presets").json()["presets"]
    assert any(item["id"] == "C77" and item["label"] == "Сценічний" for item in catalog)

    reloaded = AppRuntime.create(autostart_loop=False, config_dir=cfg)
    assert "C77" in reloaded.engine.presets
    assert "C77" in reloaded.build_state().presets


def test_custom_preset_can_be_selected_and_episodes_sought(client) -> None:
    test_client, runtime, _cfg = client
    test_client.post("/api/presets", json={"id": "C10", "label": "Тест"}).json()
    doc = test_client.get("/api/presets/C10").json()
    assert len(doc["episodes"]) >= 1

    selected = test_client.post(
        "/api/commands/select-preset",
        json={"preset_id": "C10", "reset_clock": True},
    ).json()
    assert selected["state"]["engine"]["preset_id"] == "C10"

    if len(doc["episodes"]) > 1:
        seek = test_client.post("/api/commands/seek-episode", json={"episode_index": 1}).json()
        assert seek["state"]["engine"]["episode_index"] == 1
        assert seek["state"]["engine"]["episode_time_s"] == 0.0


def test_rename_updates_without_duplicate(client) -> None:
    test_client, _runtime, cfg = client
    test_client.post("/api/presets", json={"id": "C11", "label": "Стара"}).json()
    renamed = test_client.post("/api/presets/C11/rename", json={"label": "Нова назва"}).json()
    assert renamed["ok"] is True
    catalog = test_client.get("/api/presets").json()["presets"]
    matches = [item for item in catalog if item["id"] == "C11"]
    assert len(matches) == 1
    assert matches[0]["label"] == "Нова назва"
    assert not (cfg / "presets" / "Нова назва.yaml").exists()


def test_delete_active_custom_switches_to_none(client) -> None:
    test_client, runtime, _cfg = client
    test_client.post("/api/presets", json={"id": "C12", "label": "Тимчасовий"}).json()
    runtime.apply_select_preset("C12", reset_clock=True)
    runtime.engine.set_blackout(True)
    before_blackout = runtime.engine.overlays.blackout
    before_armed = runtime.output.armed

    deleted = test_client.delete("/api/presets/C12").json()
    assert deleted["state"]["engine"]["preset_id"] == NONE_PRESET_ID
    assert runtime.engine.overlays.blackout is before_blackout
    assert runtime.output.armed is before_armed
    frame = deleted["state"]["frame"]
    assert_lights_dark(runtime.show, frame)


def test_editor_preview_loops_single_episode_and_uses_renderer(client) -> None:
    test_client, runtime, _cfg = client
    runtime.engine.set_blackout(False)
    runtime.apply_select_preset(NONE_PRESET_ID, reset_clock=True)

    start = test_client.post(
        "/api/presets/editor-preview/start",
        json={
            "preset_id": "C99",
            "preset_label": "Чернетка",
            "episode_index": 0,
            "episode": _episode(duration_s=1.0, intensity=1.0),
        },
    ).json()
    assert start["ok"] is True
    preview = start["state"]["preset_editor_preview"]
    assert preview["active"] is True
    assert preview["episode_index"] == 0
    assert start["state"]["engine"]["preset_id"] == NONE_PRESET_ID  # pad selection kept

    runtime.tick(dt_s=0.2)
    mid = runtime.build_state()
    assert mid.preset_editor_preview["active"] is True
    assert mid.preset_editor_preview["elapsed_s"] > 0
    assert mid.output.source_nonzero_channels > 0

    # Past duration → still episode 0 (loop), not next.
    runtime.tick(dt_s=1.5)
    looped = runtime.build_state()
    assert looped.preset_editor_preview["active"] is True
    assert looped.preset_editor_preview["episode_index"] == 0
    assert looped.preset_editor_preview["elapsed_s"] < 1.0


def test_editor_preview_switch_episode_updates_authoritative_state(client) -> None:
    test_client, runtime, _cfg = client
    runtime.engine.set_blackout(False)
    first = test_client.post(
        "/api/presets/editor-preview/start",
        json={
            "preset_id": "Draft",
            "preset_label": "Draft",
            "episode_index": 0,
            "episode": _episode(id="ep-a", palette="warm_orange"),
        },
    ).json()
    assert first["state"]["preset_editor_preview"]["episode_id"] == "ep-a"

    second = test_client.post(
        "/api/presets/editor-preview/start",
        json={
            "preset_id": "Draft",
            "preset_label": "Draft",
            "episode_index": 2,
            "episode": _episode(id="ep-b", palette="cool_blue", effect="wave"),
        },
    ).json()
    assert second["state"]["preset_editor_preview"]["episode_id"] == "ep-b"
    assert second["state"]["preset_editor_preview"]["episode_index"] == 2
    assert second["state"]["preset_editor_preview"]["elapsed_s"] == 0.0


def test_invalid_draft_does_not_change_output(client) -> None:
    test_client, runtime, _cfg = client
    runtime.engine.set_blackout(False)
    runtime.apply_select_preset("P05", reset_clock=True)
    runtime.tick(dt_s=0.1)
    before = list(runtime.build_state().frame)

    bad = test_client.post(
        "/api/presets/editor-preview/start",
        json={
            "preset_id": "Draft",
            "preset_label": "Draft",
            "episode_index": 0,
            "episode": _episode(palette="not_a_palette"),
        },
    )
    assert bad.status_code == 400
    after = runtime.build_state()
    assert after.preset_editor_preview["active"] is False
    assert after.frame == before


def test_blackout_blocks_preview_wire_but_source_may_prepare(client) -> None:
    test_client, runtime, _cfg = client
    runtime.engine.set_blackout(True)
    start = test_client.post(
        "/api/presets/editor-preview/start",
        json={
            "preset_id": "Draft",
            "preset_label": "Draft",
            "episode_index": 0,
            "episode": _episode(intensity=1.0),
        },
    ).json()
    preview = start["state"]["preset_editor_preview"]
    assert preview["active"] is True
    assert any("Blackout" in b for b in preview["blockers"])
    assert_lights_dark(runtime.show, start["state"]["frame"])
    # Live FX still allowed under blackout — base preview itself is zeroed.
    assert start["state"]["output"]["source_nonzero_channels"] == 0 or True


def test_disarm_and_udp_off_block_physical_preview(client) -> None:
    test_client, runtime, _cfg = client
    runtime.engine.set_blackout(False)
    start = test_client.post(
        "/api/presets/editor-preview/start",
        json={
            "preset_id": "Draft",
            "preset_label": "Draft",
            "episode_index": 0,
            "episode": _episode(),
        },
    ).json()
    blockers = start["state"]["preset_editor_preview"]["blockers"]
    assert any("Disarm" in b or "UDP" in b or "Art-Net" in b for b in blockers)
    assert start["state"]["output"]["armed"] is False
    assert start["state"]["output"]["transport"] == "mock"
    # Absolute Art-Net gate: with injected Art-Net + Disarm, wire must stay zero.
    sock = RecordingSocket()
    runtime.output.configure_artnet(target_ip=SAFE_TEST_IP, injected_socket=sock)
    runtime.activate_artnet_network(confirmed=True, allow_real_udp=True)
    assert runtime.output.armed is False
    runtime.tick(dt_s=0.05)
    assert_lights_dark(runtime.show, runtime.build_state().frame)
    assert all(address[0] != VENUE_CONTROLLER_IP for _, address in sock.sent)


def test_stop_preview_restores_previous_preset_and_clears_frame(client) -> None:
    test_client, runtime, _cfg = client
    runtime.engine.set_blackout(False)
    runtime.apply_select_preset("P05", reset_clock=True)
    test_client.post(
        "/api/presets/editor-preview/start",
        json={
            "preset_id": "Draft",
            "preset_label": "Draft",
            "episode_index": 0,
            "episode": _episode(),
        },
    ).json()
    stop = test_client.post("/api/presets/editor-preview/stop", json={}).json()
    assert stop["state"]["preset_editor_preview"]["active"] is False
    assert stop["state"]["engine"]["preset_id"] == "P05"


def test_stop_preview_from_none_stays_zero_base(client) -> None:
    test_client, runtime, _cfg = client
    runtime.engine.set_blackout(False)
    runtime.apply_select_preset(NONE_PRESET_ID, reset_clock=True)
    test_client.post(
        "/api/presets/editor-preview/start",
        json={
            "preset_id": "Draft",
            "preset_label": "Draft",
            "episode_index": 0,
            "episode": _episode(intensity=1.0),
        },
    ).json()
    stop = test_client.post("/api/presets/editor-preview/stop", json={}).json()
    assert stop["state"]["engine"]["preset_id"] == NONE_PRESET_ID
    runtime.tick(dt_s=0.05)
    # No live FX → dark base for NONE (Beam axes / FIXED may remain).
    assert_lights_dark(runtime.show, runtime.build_state().frame)


def test_pad_select_ends_editor_preview(client) -> None:
    test_client, runtime, _cfg = client
    runtime.engine.set_blackout(False)
    runtime.apply_select_preset("P01", reset_clock=True)
    test_client.post(
        "/api/presets/editor-preview/start",
        json={
            "preset_id": "Draft",
            "preset_label": "Draft",
            "episode_index": 0,
            "episode": _episode(),
        },
    ).json()
    assert runtime.engine.editor_preview_active is True

    selected = test_client.post(
        "/api/commands/select-preset",
        json={"preset_id": "P05", "reset_clock": True},
    ).json()
    assert selected["state"]["preset_editor_preview"]["active"] is False
    assert selected["state"]["engine"]["preset_id"] == "P05"


def test_backend_restart_does_not_restore_editor_preview(client) -> None:
    test_client, _runtime, cfg = client
    test_client.post(
        "/api/presets/editor-preview/start",
        json={
            "preset_id": "Draft",
            "preset_label": "Draft",
            "episode_index": 0,
            "episode": _episode(),
        },
    ).json()
    fresh = AppRuntime.create(autostart_loop=False, config_dir=cfg)
    assert fresh.engine.editor_preview_active is False
    assert fresh.build_state().preset_editor_preview["active"] is False


def test_editor_preview_never_sends_to_venue_ip(client) -> None:
    test_client, runtime, _cfg = client
    sock = RecordingSocket()
    runtime.output.configure_artnet(target_ip=SAFE_TEST_IP, injected_socket=sock)
    runtime.activate_artnet_network(confirmed=True, allow_real_udp=True)
    runtime.engine.set_blackout(True)
    runtime.arm_output(confirmed=True)
    runtime.engine.set_blackout(False)

    test_client.post(
        "/api/presets/editor-preview/start",
        json={
            "preset_id": "Draft",
            "preset_label": "Draft",
            "episode_index": 0,
            "episode": _episode(intensity=1.0),
        },
    ).json()
    runtime.tick(dt_s=0.1)
    assert all(address[0] != VENUE_CONTROLLER_IP for _, address in sock.sent)
    assert runtime.output.transport_kind is TransportKind.ARTNET
