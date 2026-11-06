"""Input adapter mapping, debounce and disconnect safety tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.config import default_scene_dir
from orng_led.input.adapters import GpioInputAdapterStub, KeyboardInputAdapter, MockInputAdapter
from orng_led.input.contract import (
    BRIGHTNESS_STEP,
    LIVE_FX_SPEED_MIN,
    InputAction,
    InputSource,
)
from orng_led.input.debounce import EdgeDebouncer
from orng_led.input.mapping import button_to_event
from orng_led.main import create_app


@pytest.fixture
def isolated_config(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(default_scene_dir(), target)
    return target


@pytest.fixture
def client(isolated_config: Path):
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as test_client:
        yield test_client, runtime


def test_buttons_1_to_9_select_pad_slots_and_10_is_none(client) -> None:
    test_client, runtime = client
    pad = list(runtime.show.app.pad_presets)
    assert len(pad) == 9
    for button_id in range(1, 10):
        ack = test_client.post(
            "/api/input/button",
            json={"button_id": button_id, "source": "mock"},
        ).json()
        assert ack["accepted"] is True
        assert ack["state"]["engine"]["preset_id"] == pad[button_id - 1]
    none = test_client.post(
        "/api/input/button",
        json={"button_id": 10, "source": "mock"},
    ).json()
    assert none["accepted"] is True
    assert none["state"]["engine"]["preset_id"] == "NONE"
    assert runtime.input_dispatcher is not None


def test_pointer_release_clears_strobe_and_sweeps(client) -> None:
    test_client, runtime = client
    source = "web:desk"
    strobe = test_client.post(
        "/api/input/button",
        json={
            "button_id": 13,
            "source": "ui",
            "edge": "press",
            "source_id": source,
            "client_command_id": "strobe-press",
        },
    ).json()
    assert strobe["accepted"] is True
    assert runtime.engine.overlays.strobe_held is True
    released = test_client.post(
        "/api/input/button",
        json={
            "button_id": 13,
            "source": "ui",
            "edge": "release",
            "source_id": source,
            "client_command_id": "strobe-release",
        },
    ).json()
    assert released["accepted"] is True
    assert runtime.engine.overlays.strobe_held is False

    for action in ("sweep", "vertical_sweep"):
        pressed = test_client.post(
            "/api/input/action",
            json={
                "action": f"{action}_press",
                "edge": "press",
                "source": "ui",
                "source_id": source,
                "client_command_id": f"{action}-press",
            },
        ).json()
        assert pressed["accepted"] is True
        assert runtime.engine.overlays.sweep_held is True
        up = test_client.post(
            "/api/input/action",
            json={
                "action": f"{action}_release",
                "edge": "release",
                "source": "ui",
                "source_id": source,
                "client_command_id": f"{action}-release",
            },
        ).json()
        assert up["accepted"] is True
        assert runtime.engine.overlays.sweep_held is False


def test_strobe_press_release_and_key_repeat(client) -> None:
    # orng-bar CH57x remap: physical Z → HID KeyQ → strobe
    test_client, runtime = client
    press = test_client.post(
        "/api/input/keyboard",
        json={"code": "KeyQ", "type": "keydown", "repeat": False},
    ).json()
    assert press["accepted"] is True
    assert press["state"]["engine"]["strobe_held"] is True

    repeat = test_client.post(
        "/api/input/keyboard",
        json={"code": "KeyQ", "type": "keydown", "repeat": True},
    ).json()
    assert repeat["accepted"] is False
    assert runtime.engine.overlays.strobe_held is True

    dup = test_client.post(
        "/api/input/keyboard",
        json={"code": "KeyQ", "type": "keydown", "repeat": False},
    ).json()
    assert dup["accepted"] is False
    assert runtime.engine.overlays.strobe_held is True

    release = test_client.post(
        "/api/input/keyboard",
        json={"code": "KeyQ", "type": "keyup", "repeat": False},
    ).json()
    assert release["accepted"] is True
    assert release["state"]["engine"]["strobe_held"] is False


def test_brightness_steps(client) -> None:
    test_client, runtime = client
    runtime.engine.set_master_brightness(0.5)
    down = test_client.post("/api/input/button", json={"button_id": 15}).json()
    assert down["accepted"] is True
    assert down["state"]["engine"]["master_brightness"] == pytest.approx(0.5 - BRIGHTNESS_STEP)
    up = test_client.post("/api/input/button", json={"button_id": 16}).json()
    assert up["state"]["engine"]["master_brightness"] == pytest.approx(0.5)


def test_face_white_hit_blackout_via_contract(client) -> None:
    test_client, runtime = client
    before_face = runtime.engine.overlays.face_on
    face = test_client.post("/api/input/button", json={"button_id": 11}).json()
    assert face["state"]["engine"]["face_on"] is (not before_face)
    hit = test_client.post("/api/input/button", json={"button_id": 12}).json()
    assert hit["accepted"] is True
    # Startup blackout is on; clear then re-engage via pad.
    runtime.engine.set_blackout(False)
    bo = test_client.post("/api/input/button", json={"button_id": 14}).json()
    assert bo["state"]["engine"]["blackout"] is True


def test_debounce_pulse_and_mapping_helpers() -> None:
    debouncer = EdgeDebouncer(min_interval_s=0.1)
    assert debouncer.accept_pulse("b1", 1.0) is True
    assert debouncer.accept_pulse("b1", 1.05) is False
    assert debouncer.accept_press("strobe", 2.0) is True
    assert debouncer.accept_press("strobe", 2.2) is False
    assert debouncer.accept_release("strobe", 2.3) is True
    assert debouncer.accept_release("strobe", 2.4) is False

    event = button_to_event(3, source=InputSource.UI)
    assert event.action is InputAction.SELECT_PAD_SLOT
    assert event.pad_slot == 2

    none = button_to_event(10, source=InputSource.UI)
    assert none.action is InputAction.SELECT_PRESET
    assert none.preset_id == "NONE"

    kb = KeyboardInputAdapter()
    assert kb.handle_raw({"code": "KeyT", "type": "keydown", "repeat": False})
    assert kb.handle_raw({"code": "KeyT", "type": "keydown", "repeat": True}) == []

    gpio = GpioInputAdapterStub()
    assert gpio.implemented is False
    assert gpio.poll() == []


def test_disconnect_clears_strobe_hold(client) -> None:
    test_client, runtime = client
    source_id = "web:test-session"
    test_client.post(
        "/api/input/button",
        json={"button_id": 13, "edge": "press", "source_id": source_id},
    )
    assert runtime.engine.overlays.strobe_held is True
    runtime.on_ws_disconnect(source_id=source_id)
    assert runtime.engine.overlays.strobe_held is False
    assert runtime.input_dispatcher is not None
    assert runtime.input_dispatcher.holds.any_held("strobe") is False


def test_mapping_endpoint_lists_keyboard_and_gpio_boundary(client) -> None:
    test_client, runtime = client
    body = test_client.get("/api/input/mapping").json()
    assert body["buttons"] == list(range(1, 17))
    assert body["pad_presets"] == list(runtime.show.app.pad_presets)
    assert body["gpio"]["implemented"] is False
    # Active scene is orng-bar (CH57x HID remap), not HOME letter codes.
    assert body["keyboard"]["KeyR"]["action"] == "select_pad_slot"
    assert body["keyboard"]["KeyQ"]["action"] == "strobe_press"
    assert body["keyboard"]["KeyP"]["action"] == "select_pad_slot"  # physical O
    assert body["keyboard"]["KeyX"]["action"] == "blackout_toggle"
    assert body["keyboard"]["AudioVolumeMute"]["action"] == "face_toggle"
    assert body["keyboard"]["Escape"]["action"] == "face_toggle"
    assert body["keyboard"]["KeyZ"]["action"] == "face_toggle"
    assert body["keyboard"]["KeyO"]["action"] == "vertical_sweep_press"
    assert "Backspace" not in body["keyboard"]
    assert "Delete" not in body["keyboard"]


def test_speed_encoder_mute_toggles_face_and_c_holds_vertical_sweep(client) -> None:
    # orng-bar: speed-encoder click → Mute (face); physical C → KeyO (vertical)
    test_client, runtime = client
    before_face = runtime.engine.overlays.face_on
    mute = test_client.post(
        "/api/input/keyboard",
        json={"code": "AudioVolumeMute", "type": "keydown", "repeat": False},
    ).json()
    assert mute["accepted"] is True
    assert runtime.engine.overlays.face_on is (not before_face)

    test_client.post(
        "/api/input/keyboard",
        json={"code": "KeyO", "type": "keydown", "repeat": False},
    )
    assert runtime.engine.overlays.sweep_held is True
    assert runtime.engine.overlays.sweep_mode == "vertical"
    test_client.post(
        "/api/input/keyboard",
        json={"code": "KeyO", "type": "keyup", "repeat": False},
    )
    assert runtime.engine.overlays.sweep_held is False


def test_ui_source_goes_through_dispatcher(client) -> None:
    _test_client, runtime = client
    dispatcher = runtime.input_dispatcher
    assert dispatcher is not None
    mock = MockInputAdapter()
    event = mock.press_button(1, client_command_id="ui-1")
    result = dispatcher.dispatch(event)
    assert result.accepted is True
    assert result.state is not None
    assert result.state.engine.preset_id == runtime.show.app.pad_presets[0]


def test_live_fx_speed_cannot_go_below_five_percent(client) -> None:
    _test_client, runtime = client
    runtime.engine.set_strobe_speed(0.0)
    runtime.engine.set_sweep_speed(-1.0)
    assert runtime.engine.overlays.strobe_speed == pytest.approx(0.05)
    assert runtime.engine.overlays.sweep_speed == pytest.approx(0.05)


def test_live_fx_speed_resets_to_default_each_hold(client) -> None:
    _test_client, runtime = client
    default_strobe = float(runtime.show.app.strobe_speed)
    default_sweep = float(runtime.show.app.sweep_speed)

    runtime.engine.strobe_press()
    runtime.apply_nudge_live_fx_speed(-0.2)
    assert runtime.engine.overlays.strobe_speed == pytest.approx(
        max(LIVE_FX_SPEED_MIN, default_strobe - 0.2)
    )
    assert runtime.show.app.strobe_speed == pytest.approx(default_strobe)
    runtime.engine.strobe_release()
    assert runtime.engine.overlays.strobe_speed == pytest.approx(default_strobe)

    runtime.engine.strobe_press()
    assert runtime.engine.overlays.strobe_speed == pytest.approx(default_strobe)
    runtime.engine.strobe_release()

    runtime.engine.sweep_press()
    runtime.apply_nudge_live_fx_speed(-0.15)
    assert runtime.engine.overlays.sweep_speed == pytest.approx(
        max(LIVE_FX_SPEED_MIN, default_sweep - 0.15)
    )
    assert runtime.show.app.sweep_speed == pytest.approx(default_sweep)
    runtime.engine.sweep_release()
    assert runtime.engine.overlays.sweep_speed == pytest.approx(default_sweep)
    runtime.engine.sweep_press()
    assert runtime.engine.overlays.sweep_speed == pytest.approx(default_sweep)


def test_volume_encoder_nudges_live_fx_speed_while_strobe_or_sweep_held(client) -> None:
    test_client, runtime = client
    runtime.apply_preview_speed(1.0)
    runtime.engine.set_strobe_speed(0.5)
    runtime.engine.set_sweep_speed(0.5)

    test_client.post(
        "/api/input/keyboard",
        json={"code": "KeyQ", "type": "keydown", "repeat": False},
    )
    before_strobe = runtime.engine.overlays.strobe_speed
    up = test_client.post(
        "/api/input/keyboard",
        json={"code": "AudioVolumeUp", "type": "keydown", "repeat": False},
    ).json()
    assert up["accepted"] is True
    assert runtime.preview_speed == pytest.approx(1.0)
    assert runtime.engine.overlays.strobe_speed == pytest.approx(before_strobe + 0.05)
    test_client.post(
        "/api/input/keyboard",
        json={"code": "KeyQ", "type": "keyup", "repeat": False},
    )

    test_client.post(
        "/api/input/keyboard",
        json={"code": "KeyT", "type": "keydown", "repeat": False},
    )
    before_sweep = runtime.engine.overlays.sweep_speed
    program_before = runtime.preview_speed
    down = test_client.post(
        "/api/input/keyboard",
        json={"code": "AudioVolumeDown", "type": "keydown", "repeat": False},
    ).json()
    assert down["accepted"] is True
    assert runtime.preview_speed == pytest.approx(program_before)
    assert runtime.engine.overlays.sweep_speed == pytest.approx(before_sweep - 0.05)


def test_zoom_encoder_changes_episode_while_sweep_held(client) -> None:
    """Ctrl+wheel / Zoom must step episodes without releasing or zeroing Sweep."""
    test_client, runtime = client
    runtime.apply_select_preset("P05", reset_clock=True)
    runtime.apply_seek_episode(1)
    # orng-bar: physical X → KeyT → horizontal sweep
    test_client.post(
        "/api/input/keyboard",
        json={"code": "KeyT", "type": "keydown", "repeat": False},
    )
    assert runtime.engine.overlays.sweep_held is True
    before_speed = runtime.engine.overlays.sweep_speed
    before_episode = runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0.0).episode_index

    zoom = test_client.post(
        "/api/input/keyboard",
        json={"code": "ZoomIn", "type": "keydown", "repeat": False},
    ).json()
    assert zoom["accepted"] is True
    after = runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0.0)
    assert runtime.engine.overlays.sweep_held is True
    assert runtime.engine.overlays.sweep_speed == pytest.approx(before_speed)
    assert after.episode_index == before_episode + 1
    assert after.sweep_active is True
