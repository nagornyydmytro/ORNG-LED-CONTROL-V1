"""Same mock executor for local commands and the desk mapping. No live device."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime, ManualShowLockedError
from orng_led.desktop.remote_desk import command_for_request
from orng_led.input.contract import InputAction, InputEvent, InputSource
from orng_led.main import create_app


def _press(runtime: AppRuntime, action: InputAction, source_id: str, edge: str = "press"):
    return runtime.input_dispatcher.dispatch(
        InputEvent(action=action, source=InputSource.UI, edge=edge, source_id=source_id)
    )


def test_preset_keeps_manual_fx_and_auto_keeps_holds(tmp_path) -> None:
    """Strobe, sweep and Face stay available in Auto. A preset pick does not."""
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.engine.set_blackout(False)
    runtime.apply_bpm_sync(enabled=True, scope="preset", palette="orange")
    before = runtime.engine.overlays.strobe_speed
    _state, replay = runtime.apply_live_fx_speeds(
        strobe_speed=min(1.0, before + 0.2),
        persist=False,
    )
    assert replay is False
    assert runtime.engine.overlays.strobe_speed != before
    assert _press(runtime, InputAction.STROBE_PRESS, "web:a").accepted is True
    assert _press(runtime, InputAction.SWEEP_PRESS, "web:a").accepted is True
    runtime.apply_face(True, 1.0)
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.engine.overlays.sweep_held is True
    assert runtime.engine.overlays.face_on is True

    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.engine.overlays.sweep_held is True
    assert runtime.engine.overlays.face_on is True
    assert _press(runtime, InputAction.STROBE_PRESS, "web:b").accepted is True
    assert _press(runtime, InputAction.SWEEP_PRESS, "web:b").accepted is True
    _press(runtime, InputAction.STROBE_RELEASE, "web:a", edge="release")
    _press(runtime, InputAction.SWEEP_RELEASE, "web:a", edge="release")
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.engine.overlays.sweep_held is True
    _press(runtime, InputAction.STROBE_RELEASE, "web:b", edge="release")
    _press(runtime, InputAction.SWEEP_RELEASE, "web:b", edge="release")
    assert runtime.engine.overlays.strobe_held is False
    assert runtime.engine.overlays.sweep_held is False

    runtime.apply_bpm_sync(scope="preset")
    assert _press(runtime, InputAction.STROBE_PRESS, "web:a").accepted is True
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    assert runtime.engine.overlays.strobe_held is True
    held_preset = runtime.engine.active_preset_id
    with pytest.raises(ManualShowLockedError):
        runtime.apply_select_preset("P01")
    assert runtime.engine.active_preset_id == held_preset
    runtime.apply_face(False, 0.0)
    runtime.apply_live_fx_speeds(strobe_speed=0.4, persist=False)
    runtime.apply_blackout(True)
    assert runtime.engine.overlays.blackout is True
    assert runtime.engine.overlays.strobe_held is True
    snap = runtime.engine.render_at(1.0, dt_s=1.0 / 30.0)
    assert snap.blackout is True
    assert snap.frame
    runtime.apply_blackout(False)
    _press(runtime, InputAction.STROBE_RELEASE, "web:a", edge="release")
    assert runtime.engine.overlays.strobe_held is False
    runtime.apply_bpm_sync(scope="preset")
    assert runtime.bpm.scope == "preset"
    assert runtime.engine.auto_base is None

    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app) as client:
        runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
        rejected = client.post("/api/commands/select-preset", json={"preset_id": "P02"})
        assert rejected.status_code == 409
        assert rejected.json()["status"] == "rejected"
        assert rejected.json()["status"] != "applied"


def test_two_holds_release_separately_and_disconnect_does_not_repeat(tmp_path) -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.apply_bpm_sync(enabled=True, scope="preset")
    _press(runtime, InputAction.STROBE_PRESS, "web:a")
    _press(runtime, InputAction.STROBE_PRESS, "web:b")
    assert runtime.input_dispatcher.holds.is_held_by("strobe", "web:a")
    assert runtime.input_dispatcher.holds.is_held_by("strobe", "web:b")
    _press(runtime, InputAction.STROBE_RELEASE, "web:stranger", edge="release")
    assert runtime.input_dispatcher.holds.is_held_by("strobe", "web:a")
    assert runtime.input_dispatcher.holds.is_held_by("strobe", "web:b")
    _press(runtime, InputAction.STROBE_RELEASE, "web:a", edge="release")
    assert runtime.engine.overlays.strobe_held is True
    runtime.on_ws_disconnect(source_id="web:b")
    assert runtime.engine.overlays.strobe_held is False
    runtime.on_ws_disconnect(source_id="web:b")
    assert runtime.engine.overlays.strobe_held is False


def test_desk_mapping_uses_the_callers_source() -> None:
    mapped = command_for_request(
        "/api/input/action",
        {"action": "strobe_press", "edge": "press", "source_id": "web:a"},
    )
    assert mapped is not None
    assert mapped[1]["source"] == "cloud:desk:web:a"
    other = command_for_request(
        "/api/input/action",
        {"action": "strobe_release", "edge": "release", "source_id": "web:b"},
    )
    assert other is not None
    assert other[1]["source"] != mapped[1]["source"]
