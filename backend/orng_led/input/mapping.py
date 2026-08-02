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
# Digits 1–9 → pad slots; Digit0 → NONE; Space blackout; Backspace strobe; Enter sweep.
# Zoom encoder → episode / live-FX speed; Volume encoder → program speed ×1–×5.
KEYBOARD_CODE_MAP: dict[str, tuple[InputAction, Edge]] = {
    "Digit1": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "Digit2": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "Digit3": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "Digit4": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "Digit5": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "Digit6": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "Digit7": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "Digit8": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "Digit9": (InputAction.SELECT_PAD_SLOT, "pulse"),
    "Digit0": (InputAction.SELECT_PRESET, "pulse"),
    "Space": (InputAction.BLACKOUT_TOGGLE, "pulse"),
    "Backspace": (InputAction.STROBE_PRESS, "press"),
    "Enter": (InputAction.SWEEP_PRESS, "press"),
    "NumpadEnter": (InputAction.SWEEP_PRESS, "press"),
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

DIGIT_PAD_SLOT: dict[str, int] = {f"Digit{n}": n - 1 for n in range(1, 10)}

HOLD_RELEASE_CODES: dict[str, InputAction] = {
    "Backspace": InputAction.STROBE_RELEASE,
    "Enter": InputAction.SWEEP_RELEASE,
    "NumpadEnter": InputAction.SWEEP_RELEASE,
}

# Back-compat alias used by older tests/docs.
STROBE_RELEASE_CODES = frozenset({"Backspace"})
