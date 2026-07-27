"""Input adapters: shared contract for UI, keyboard, mock and future GPIO."""

from orng_led.input.adapters import (
    GpioInputAdapterStub,
    KeyboardInputAdapter,
    MockInputAdapter,
)
from orng_led.input.contract import (
    BRIGHTNESS_STEP,
    InputAction,
    InputEvent,
    InputSource,
)
from orng_led.input.debounce import EdgeDebouncer
from orng_led.input.dispatcher import DispatchResult, InputDispatcher
from orng_led.input.mapping import KEYBOARD_CODE_MAP, button_to_event

__all__ = [
    "BRIGHTNESS_STEP",
    "DispatchResult",
    "EdgeDebouncer",
    "GpioInputAdapterStub",
    "InputAction",
    "InputDispatcher",
    "InputEvent",
    "InputSource",
    "KEYBOARD_CODE_MAP",
    "KeyboardInputAdapter",
    "MockInputAdapter",
    "button_to_event",
]
