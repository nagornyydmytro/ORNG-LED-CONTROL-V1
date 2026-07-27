"""16-button pad mapping → InputEvent actions."""

from __future__ import annotations

from orng_led.input.contract import (
    ALL_BUTTON_IDS,
    BUTTON_BLACKOUT,
    BUTTON_BRIGHTNESS_DOWN,
    BUTTON_BRIGHTNESS_UP,
    BUTTON_FACE,
    BUTTON_PRESET_IDS,
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

    if button_id in BUTTON_PRESET_IDS:
        return InputEvent(
            action=InputAction.SELECT_PRESET,
            source=source,
            button_id=button_id,
            preset_id=BUTTON_PRESET_IDS[button_id],
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
# Strobe uses KeyS: keydown → press, keyup → release.
KEYBOARD_CODE_MAP: dict[str, tuple[int, Edge]] = {
    "Digit1": (1, "pulse"),
    "Digit2": (2, "pulse"),
    "Digit3": (3, "pulse"),
    "Digit4": (4, "pulse"),
    "Digit5": (5, "pulse"),
    "Digit6": (6, "pulse"),
    "Digit7": (7, "pulse"),
    "Digit8": (8, "pulse"),
    "Digit9": (9, "pulse"),
    "Digit0": (10, "pulse"),
    "KeyF": (BUTTON_FACE, "pulse"),
    "KeyH": (BUTTON_WHITE_HIT, "pulse"),
    "KeyS": (BUTTON_STROBE, "press"),
    "KeyB": (BUTTON_BLACKOUT, "pulse"),
    "Minus": (BUTTON_BRIGHTNESS_DOWN, "pulse"),
    "Equal": (BUTTON_BRIGHTNESS_UP, "pulse"),
    "NumpadSubtract": (BUTTON_BRIGHTNESS_DOWN, "pulse"),
    "NumpadAdd": (BUTTON_BRIGHTNESS_UP, "pulse"),
}

STROBE_RELEASE_CODES = frozenset({"KeyS"})
