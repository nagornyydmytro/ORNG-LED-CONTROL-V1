"""QWERTY and CH57x are separate scene profiles."""

from orng_led.input.adapters import (
    KeyboardInputAdapter,
    adapter_for_hid,
    adapter_for_profile,
)
from orng_led.input.contract import InputAction
from orng_led.scenes.store import SceneStore


def _controls():
    return SceneStore().load_scene("orng-bar").controls


def test_qwerty_profile_does_not_use_ch57x_codes() -> None:
    qwerty = adapter_for_profile(_controls(), "qwerty")
    pad = qwerty.handle_raw({"code": "KeyQ", "type": "keydown", "repeat": False})
    assert pad and pad[0].action is InputAction.SELECT_PAD_SLOT
    assert pad[0].pad_slot == 0
    strobe = qwerty.handle_raw({"code": "KeyZ", "type": "keydown", "repeat": False})
    assert strobe and strobe[0].action is InputAction.STROBE_PRESS
    blackout = qwerty.handle_raw({"code": "Space", "type": "keydown", "repeat": False})
    assert blackout and blackout[0].action is InputAction.BLACKOUT_TOGGLE
    none = qwerty.handle_raw({"code": "KeyP", "type": "keydown", "repeat": False})
    assert none and none[0].preset_id == "NONE"


def test_ch57x_profile_keeps_observed_hid_map() -> None:
    pad = adapter_for_hid(_controls(), "1189", "8890")
    strobe = pad.handle_raw({"code": "KeyQ", "type": "keydown", "repeat": False})
    assert strobe and strobe[0].action is InputAction.STROBE_PRESS
    slot = pad.handle_raw({"code": "KeyR", "type": "keydown", "repeat": False})
    assert slot and slot[0].action is InputAction.SELECT_PAD_SLOT
    assert slot[0].pad_slot == 0
    blackout = pad.handle_raw({"code": "KeyX", "type": "keydown", "repeat": False})
    assert blackout and blackout[0].action is InputAction.BLACKOUT_TOGGLE


def test_unknown_usb_keyboard_is_qwerty() -> None:
    board = adapter_for_hid(_controls(), "046d", "c31c")
    event = board.handle_raw({"code": "KeyQ", "type": "keydown", "repeat": False})
    assert event and event[0].action is InputAction.SELECT_PAD_SLOT


def _event(adapter, code: str, edge: str):  # type: ignore[no-untyped-def]
    events = adapter.handle_raw({"code": code, "type": edge, "repeat": False})
    assert events, f"{code} {edge} produced no event"
    return events[0]


def test_qwerty_mapper_covers_pad_face_holds_speed_and_episodes() -> None:
    board = adapter_for_profile(_controls(), "qwerty")
    assert _event(board, "KeyQ", "keydown").pad_slot == 0
    assert _event(board, "KeyO", "keydown").pad_slot == 8
    assert _event(board, "KeyP", "keydown").preset_id == "NONE"
    assert _event(board, "Space", "keydown").action is InputAction.BLACKOUT_TOGGLE
    assert _event(board, "Escape", "keydown").action is InputAction.FACE_TOGGLE
    assert _event(board, "KeyZ", "keydown").action is InputAction.STROBE_PRESS
    assert _event(board, "KeyZ", "keyup").action is InputAction.STROBE_RELEASE
    assert _event(board, "KeyX", "keydown").action is InputAction.SWEEP_PRESS
    assert _event(board, "KeyX", "keyup").action is InputAction.SWEEP_RELEASE
    assert _event(board, "KeyC", "keydown").action is InputAction.VERTICAL_SWEEP_PRESS
    assert _event(board, "KeyC", "keyup").action is InputAction.VERTICAL_SWEEP_RELEASE
    assert _event(board, "AudioVolumeUp", "keydown").action is InputAction.PROGRAM_SPEED_UP
    assert _event(board, "ZoomIn", "keydown").action is InputAction.ZOOM_UP
    assert _event(board, "ZoomOut", "keydown").action is InputAction.ZOOM_DOWN


def test_ch57x_mapper_keeps_its_own_holds_and_still_has_face_speed_episodes() -> None:
    board = adapter_for_hid(_controls(), "1189", "8890")
    assert _event(board, "KeyR", "keydown").action is InputAction.SELECT_PAD_SLOT
    assert _event(board, "KeyQ", "keydown").action is InputAction.STROBE_PRESS
    assert _event(board, "KeyQ", "keyup").action is InputAction.STROBE_RELEASE
    assert _event(board, "KeyT", "keydown").action is InputAction.SWEEP_PRESS
    assert _event(board, "KeyT", "keyup").action is InputAction.SWEEP_RELEASE
    assert _event(board, "KeyO", "keydown").action is InputAction.VERTICAL_SWEEP_PRESS
    assert _event(board, "KeyO", "keyup").action is InputAction.VERTICAL_SWEEP_RELEASE
    assert _event(board, "KeyX", "keydown").action is InputAction.BLACKOUT_TOGGLE
    assert _event(board, "KeyZ", "keydown").action is InputAction.FACE_TOGGLE
    assert _event(board, "AudioVolumeMute", "keydown").action is InputAction.FACE_TOGGLE
    assert _event(board, "Escape", "keydown").action is InputAction.FACE_TOGGLE
    assert _event(board, "AudioVolumeDown", "keydown").action is InputAction.PROGRAM_SPEED_DOWN
    assert _event(board, "ZoomIn", "keydown").action is InputAction.ZOOM_UP
    assert _event(board, "KeyQ", "keydown").action is not InputAction.SELECT_PAD_SLOT


def test_ch57x_keeps_scene_keyz_and_does_not_rewrite_other_profiles() -> None:
    stale = KeyboardInputAdapter(
        code_map={"KeyZ": (InputAction.FACE_TOGGLE, "pulse")},
    )
    event = stale.handle_raw({"code": "KeyZ", "type": "keydown", "repeat": False})
    assert event and event[0].action is InputAction.FACE_TOGGLE
    qwerty = adapter_for_profile(_controls(), "qwerty")
    strobe = qwerty.handle_raw({"code": "KeyZ", "type": "keydown", "repeat": False})
    assert strobe and strobe[0].action is InputAction.STROBE_PRESS


def test_unknown_profile_is_rejected() -> None:
    try:
        adapter_for_profile(_controls(), "other-pad")
    except ValueError as exc:
        assert "unknown_profile" in str(exc)
    else:
        raise AssertionError("unknown profile was accepted")
