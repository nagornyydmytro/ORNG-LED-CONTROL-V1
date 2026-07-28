"""Input adapter mapping, debounce and disconnect safety tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.config import default_config_dir
from orng_led.input.adapters import GpioInputAdapterStub, KeyboardInputAdapter, MockInputAdapter
from orng_led.input.contract import BRIGHTNESS_STEP, InputAction, InputSource
from orng_led.input.debounce import EdgeDebouncer
from orng_led.input.mapping import button_to_event
from orng_led.main import create_app


@pytest.fixture
def isolated_config(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(default_config_dir(), target)
    return target


@pytest.fixture
def client(isolated_config: Path):
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    application = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(application) as test_client:
        yield test_client, runtime


def test_buttons_1_to_10_select_presets(client) -> None:
    test_client, runtime = client
    for button_id in range(1, 11):
        ack = test_client.post(
            "/api/input/button",
            json={"button_id": button_id, "source": "mock"},
        ).json()
        assert ack["accepted"] is True
        assert ack["state"]["engine"]["preset_id"] == f"P{button_id:02d}"
    assert runtime.input_dispatcher is not None


def test_strobe_press_release_and_key_repeat(client) -> None:
    test_client, runtime = client
    press = test_client.post(
        "/api/input/keyboard",
        json={"code": "KeyS", "type": "keydown", "repeat": False},
    ).json()
    assert press["accepted"] is True
    assert press["state"]["engine"]["strobe_held"] is True

    repeat = test_client.post(
        "/api/input/keyboard",
        json={"code": "KeyS", "type": "keydown", "repeat": True},
    ).json()
    assert repeat["accepted"] is False
    assert runtime.engine.overlays.strobe_held is True

    dup = test_client.post(
        "/api/input/keyboard",
        json={"code": "KeyS", "type": "keydown", "repeat": False},
    ).json()
    assert dup["accepted"] is False
    assert runtime.engine.overlays.strobe_held is True

    release = test_client.post(
        "/api/input/keyboard",
        json={"code": "KeyS", "type": "keyup", "repeat": False},
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
    assert event.action is InputAction.SELECT_PRESET
    assert event.preset_id == "P03"

    kb = KeyboardInputAdapter()
    assert kb.handle_raw({"code": "Digit5", "type": "keydown", "repeat": False})
    assert kb.handle_raw({"code": "Digit5", "type": "keydown", "repeat": True}) == []

    gpio = GpioInputAdapterStub()
    assert gpio.implemented is False
    assert gpio.poll() == []


def test_disconnect_clears_strobe_hold(client) -> None:
    test_client, runtime = client
    test_client.post("/api/input/button", json={"button_id": 13, "edge": "press"})
    assert runtime.engine.overlays.strobe_held is True
    runtime.on_ws_disconnect()
    assert runtime.engine.overlays.strobe_held is False
    assert runtime.input_dispatcher is not None
    assert runtime.input_dispatcher.debouncer.is_held("strobe") is False


def test_mapping_endpoint_lists_keyboard_and_gpio_boundary(client) -> None:
    test_client, _runtime = client
    body = test_client.get("/api/input/mapping").json()
    assert body["buttons"] == list(range(1, 17))
    assert body["presets"]["1"] == "P01"
    assert body["gpio"]["implemented"] is False
    assert "Digit1" in body["keyboard"]


def test_ui_source_goes_through_dispatcher(client) -> None:
    _test_client, runtime = client
    dispatcher = runtime.input_dispatcher
    assert dispatcher is not None
    mock = MockInputAdapter()
    event = mock.press_button(1, client_command_id="ui-1")
    result = dispatcher.dispatch(event)
    assert result.accepted is True
    assert result.state is not None
    assert result.state.engine.preset_id == "P01"
