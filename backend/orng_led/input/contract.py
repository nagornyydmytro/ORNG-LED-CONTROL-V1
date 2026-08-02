"""Shared input event contract (UI, keyboard, mock, future GPIO)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class InputSource(StrEnum):
    UI = "ui"
    KEYBOARD = "keyboard"
    MOCK = "mock"
    GPIO = "gpio"


class InputAction(StrEnum):
    SELECT_PRESET = "select_preset"
    SELECT_PAD_SLOT = "select_pad_slot"
    FACE_TOGGLE = "face_toggle"
    WHITE_HIT = "white_hit"
    STROBE_PRESS = "strobe_press"
    STROBE_RELEASE = "strobe_release"
    SWEEP_PRESS = "sweep_press"
    SWEEP_RELEASE = "sweep_release"
    VERTICAL_SWEEP_PRESS = "vertical_sweep_press"
    VERTICAL_SWEEP_RELEASE = "vertical_sweep_release"
    BLACKOUT_TOGGLE = "blackout_toggle"
    BRIGHTNESS_DOWN = "brightness_down"
    BRIGHTNESS_UP = "brightness_up"
    EPISODE_PREV = "episode_prev"
    EPISODE_NEXT = "episode_next"
    PROGRAM_SPEED_DOWN = "program_speed_down"
    PROGRAM_SPEED_UP = "program_speed_up"
    LIVE_FX_SPEED_DOWN = "live_fx_speed_down"
    LIVE_FX_SPEED_UP = "live_fx_speed_up"
    # Zoom encoder: episode ±1, or live-FX speed while a hold effect is active.
    ZOOM_DOWN = "zoom_down"
    ZOOM_UP = "zoom_up"


Edge = Literal["press", "release", "pulse"]


@dataclass(frozen=True)
class InputEvent:
    """Transport-agnostic control event.

    The lighting engine never reads Vue/DOM directly. Adapters emit InputEvent
    values that the dispatcher applies through AppRuntime.
    """

    action: InputAction
    source: InputSource
    button_id: int | None = None
    preset_id: str | None = None
    pad_slot: int | None = None
    edge: Edge | None = None
    client_command_id: str | None = None


BRIGHTNESS_STEP = 0.05
BRIGHTNESS_MIN = 0.0
BRIGHTNESS_MAX = 1.0
LIVE_FX_SPEED_STEP = 0.05
PROGRAM_SPEED_MIN = 1
PROGRAM_SPEED_MAX = 5
PAD_SLOT_COUNT = 9

# Physical pad layout from canon §9 (presets 1–9 → home pad slots; 10 → NONE).
BUTTON_PAD_SLOTS: dict[int, int] = {index: index - 1 for index in range(1, 10)}
BUTTON_NONE = 10
BUTTON_FACE = 11
BUTTON_WHITE_HIT = 12
BUTTON_STROBE = 13
BUTTON_BLACKOUT = 14
BUTTON_BRIGHTNESS_DOWN = 15
BUTTON_BRIGHTNESS_UP = 16
ALL_BUTTON_IDS = tuple(range(1, 17))

# Legacy alias — pad slots no longer hard-map to P01–P10.
BUTTON_PRESET_IDS: dict[int, str] = {}
