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
    FACE_TOGGLE = "face_toggle"
    WHITE_HIT = "white_hit"
    STROBE_PRESS = "strobe_press"
    STROBE_RELEASE = "strobe_release"
    BLACKOUT_TOGGLE = "blackout_toggle"
    BRIGHTNESS_DOWN = "brightness_down"
    BRIGHTNESS_UP = "brightness_up"


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
    edge: Edge | None = None
    client_command_id: str | None = None


BRIGHTNESS_STEP = 0.05
BRIGHTNESS_MIN = 0.0
BRIGHTNESS_MAX = 1.0

# Physical pad layout from canon §9.
BUTTON_PRESET_IDS: dict[int, str] = {index: f"P{index:02d}" for index in range(1, 11)}
BUTTON_FACE = 11
BUTTON_WHITE_HIT = 12
BUTTON_STROBE = 13
BUTTON_BLACKOUT = 14
BUTTON_BRIGHTNESS_DOWN = 15
BUTTON_BRIGHTNESS_UP = 16
ALL_BUTTON_IDS = tuple(range(1, 17))
