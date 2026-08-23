"""16-button pad mapping → InputEvent actions."""

from __future__ import annotations

from orng_led.input.contract import (
    ALL_BUTTON_IDS,
    BUTTON_BLACKOUT,
    BUTTON_BRIGHTNESS_DOWN,
    BUTTON_BRIGHTNESS_UP,
    BUTTON_FACE,
    BUTTON_NONE,
    BUTTON_PAD_SLOTS,
    BUTTON_STROBE,
    BUTTON_WHITE_HIT,
    Edge,
    InputAction,
    InputEvent,
    InputSource,
)


def button_to_event(
    button_id: int,
    *,
    source: InputSource,
    edge: Edge = "pulse",
    client_command_id: str | None = None,
) -> InputEvent:
    if button_id not in ALL_BUTTON_IDS:
        raise ValueError(f"button_id must be 1..16 (got {button_id})")

    if button_id in BUTTON_PAD_SLOTS:
        return InputEvent(
            action=InputAction.SELECT_PAD_SLOT,
            source=source,
            button_id=button_id,
            pad_slot=BUTTON_PAD_SLOTS[button_id],
            edge="pulse",
            client_command_id=client_command_id,
        )

    if button_id == BUTTON_NONE:
        return InputEvent(
            action=InputAction.SELECT_PRESET,
            source=source,
            button_id=button_id,
            preset_id="NONE",
            edge="pulse",
            client_command_id=client_command_id,
        )

    if button_id == BUTTON_FACE:
        return InputEvent(
            action=InputAction.FACE_TOGGLE,
            source=source,
            button_id=button_id,
            edge="pulse",
            client_command_id=client_command_id,
        )

    if button_id == BUTTON_WHITE_HIT:
        return InputEvent(
            action=InputAction.WHITE_HIT,
            source=source,
            button_id=button_id,
            edge="pulse",
            client_command_id=client_command_id,
        )

    if button_id == BUTTON_STROBE:
        resolved_edge: Edge = "release" if edge == "release" else "press"
        action = (
            InputAction.STROBE_RELEASE if resolved_edge == "release" else InputAction.STROBE_PRESS
        )
        return InputEvent(
            action=action,
            source=source,
            button_id=button_id,
            edge=resolved_edge,
            client_command_id=client_command_id,
        )

    if button_id == BUTTON_BLACKOUT:
        return InputEvent(
            action=InputAction.BLACKOUT_TOGGLE,
            source=source,
            button_id=button_id,
            edge="pulse",
            client_command_id=client_command_id,
        )

    if button_id == BUTTON_BRIGHTNESS_DOWN:
        return InputEvent(
            action=InputAction.BRIGHTNESS_DOWN,
            source=source,
            button_id=button_id,
            edge="pulse",
            client_command_id=client_command_id,
        )

    if button_id == BUTTON_BRIGHTNESS_UP:
        return InputEvent(
            action=InputAction.BRIGHTNESS_UP,
            source=source,
            button_id=button_id,
            edge="pulse",
            client_command_id=client_command_id,
        )

    raise ValueError(f"Unhandled button_id {button_id}")


# Keyboard defaults for HOME adapter (Browser KeyboardEvent.code values).
# Q–O → pad slots 1–9; P → NONE;
# Space = single-press blackout toggle (pulse; repeats ignored);
# Hold FX:
#   Z = strobe, X = horizontal sweep, Delete = vertical sweep (C also works).
# Escape = face PARs toggle (KeyF also works).
# Zoom → episode ±1 (does not release or retarget live FX);
# Volume → program ×0.5–×5, or live-FX speed while Strobe/Sweep held.
KEYBOARD_CODE_MAP: dict[str, tuple[InputAction, Edge]] = {
    "KeyQ": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "KeyW": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "KeyE": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "KeyR": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "KeyT": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "KeyY": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "KeyU": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "KeyI": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "KeyO": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "KeyP": (InputAction.SELECT_PRESET, "pulse"),
    "Space": (InputAction.BLACKOUT_TOGGLE, "pulse"),
    "KeyZ": (InputAction.STROBE_PRESS, "press"),
    "KeyX": (InputAction.SWEEP_PRESS, "press"),
    "Delete": (InputAction.VERTICAL_SWEEP_PRESS, "press"),
    "KeyC": (InputAction.VERTICAL_SWEEP_PRESS, "press"),
    # Legacy aliases (less reliable for hold in the browser).
    "Backspace": (InputAction.STROBE_PRESS, "press"),
    "Enter": (InputAction.SWEEP_PRESS, "press"),
    "NumpadEnter": (InputAction.SWEEP_PRESS, "press"),
    "Escape": (InputAction.FACE_TOGGLE, "pulse"),
    "KeyF": (InputAction.FACE_TOGGLE, "pulse"),
    "Minus": (InputAction.ZOOM_DOWN, "pulse"),
    "Equal": (InputAction.ZOOM_UP, "pulse"),
    "NumpadSubtract": (InputAction.ZOOM_DOWN, "pulse"),
    "NumpadAdd": (InputAction.ZOOM_UP, "pulse"),
    "ZoomOut": (InputAction.ZOOM_DOWN, "pulse"),
    "ZoomIn": (InputAction.ZOOM_UP, "pulse"),
    "AudioVolumeDown": (InputAction.PROGRAM_SPEED_DOWN, "pulse"),
    "AudioVolumeUp": (InputAction.PROGRAM_SPEED_UP, "pulse"),
    "VolumeDown": (InputAction.PROGRAM_SPEED_DOWN, "pulse"),
    "VolumeUp": (InputAction.PROGRAM_SPEED_UP, "pulse"),
}

# Letter-row pad: Q=slot0 … O=slot8; P selects NONE (handled separately).
PAD_KEY_SLOT: dict[str, int] = {
    "KeyQ": 0,
    "KeyW": 1,
    "KeyE": 2,
    "KeyR": 3,
    "KeyT": 4,
    "KeyY": 5,
    "KeyU": 6,
    "KeyI": 7,
    "KeyO": 8,
}

# Back-compat alias for older imports/tests.
DIGIT_PAD_SLOT = PAD_KEY_SLOT

HOLD_RELEASE_CODES: dict[str, InputAction] = {
    "KeyZ": InputAction.STROBE_RELEASE,
    "KeyX": InputAction.SWEEP_RELEASE,
    "Delete": InputAction.VERTICAL_SWEEP_RELEASE,
    "KeyC": InputAction.VERTICAL_SWEEP_RELEASE,
    "Backspace": InputAction.STROBE_RELEASE,
    "Enter": InputAction.SWEEP_RELEASE,
    "NumpadEnter": InputAction.SWEEP_RELEASE,
}

# Back-compat alias used by older tests/docs.
STROBE_RELEASE_CODES = frozenset({"KeyZ", "Backspace"})
