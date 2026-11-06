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
    source_id: str | None = None,
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
            source_id=source_id,
        )

    if button_id == BUTTON_NONE:
        return InputEvent(
            action=InputAction.SELECT_PRESET,
            source=source,
            button_id=button_id,
            preset_id="NONE",
            edge="pulse",
            client_command_id=client_command_id,
            source_id=source_id,
        )

    if button_id == BUTTON_FACE:
        return InputEvent(
            action=InputAction.FACE_TOGGLE,
            source=source,
            button_id=button_id,
            edge="pulse",
            client_command_id=client_command_id,
            source_id=source_id,
        )

    if button_id == BUTTON_WHITE_HIT:
        return InputEvent(
            action=InputAction.WHITE_HIT,
            source=source,
            button_id=button_id,
            edge="pulse",
            client_command_id=client_command_id,
            source_id=source_id,
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
            source_id=source_id,
        )

    if button_id == BUTTON_BLACKOUT:
        return InputEvent(
            action=InputAction.BLACKOUT_TOGGLE,
            source=source,
            button_id=button_id,
            edge="pulse",
            client_command_id=client_command_id,
            source_id=source_id,
        )

    if button_id == BUTTON_BRIGHTNESS_DOWN:
        return InputEvent(
            action=InputAction.BRIGHTNESS_DOWN,
            source=source,
            button_id=button_id,
            edge="pulse",
            client_command_id=client_command_id,
            source_id=source_id,
        )

    if button_id == BUTTON_BRIGHTNESS_UP:
        return InputEvent(
            action=InputAction.BRIGHTNESS_UP,
            source=source,
            button_id=button_id,
            edge="pulse",
            client_command_id=client_command_id,
            source_id=source_id,
        )

    raise ValueError(f"Unhandled button_id {button_id}")


# Canonical HOME keyboard surface for every program/scene fallback:
#   Q W E R T Y U I O  — pad slots 0..8
#   P                  — NONE (logical/UI / laptop only; no key on CH57x macropad)
#   Z X C              — strobe / horizontal sweep / vertical sweep (hold)
#   Escape / Mute      — face PAR toggle (venue: speed-encoder click = Mute)
#   Space              — blackout toggle
#   Volume ±           — program speed (or live-FX speed while FX held)
#   Zoom ±             — episode ±1
# No Backspace / Delete / Enter / F on the project surface.
# Scene controls.yaml may remap HID codes (e.g. CH57x) but requested labels stay
# this set (P remains NONE in HOME defaults even when a scene reuses KeyP HID).

CANONICAL_KEYBOARD_LABELS: tuple[str, ...] = (
    "Q",
    "W",
    "E",
    "R",
    "T",
    "Y",
    "U",
    "I",
    "O",
    "P",
    "Z",
    "X",
    "C",
    "Escape",
    "Space",
    "Volume-",
    "Volume+",
    "Zoom-",
    "Zoom+",
)

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
    "KeyC": (InputAction.VERTICAL_SWEEP_PRESS, "press"),
    "Escape": (InputAction.FACE_TOGGLE, "pulse"),
    "AudioVolumeMute": (InputAction.FACE_TOGGLE, "pulse"),
    "Mute": (InputAction.FACE_TOGGLE, "pulse"),
    "Minus": (InputAction.ZOOM_DOWN, "pulse"),
    "Equal": (InputAction.ZOOM_UP, "pulse"),
    "ZoomOut": (InputAction.ZOOM_DOWN, "pulse"),
    "ZoomIn": (InputAction.ZOOM_UP, "pulse"),
    "AudioVolumeDown": (InputAction.PROGRAM_SPEED_DOWN, "pulse"),
    "AudioVolumeUp": (InputAction.PROGRAM_SPEED_UP, "pulse"),
    "VolumeDown": (InputAction.PROGRAM_SPEED_DOWN, "pulse"),
    "VolumeUp": (InputAction.PROGRAM_SPEED_UP, "pulse"),
}

# Letter-row pad: Q=slot0 … O=slot8. P is NONE (not a pad slot).
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
    "KeyC": InputAction.VERTICAL_SWEEP_RELEASE,
}

# Back-compat alias used by older tests/docs.
STROBE_RELEASE_CODES = frozenset({"KeyZ"})
