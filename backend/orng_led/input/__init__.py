"""Input adapters: shared contract for UI, keyboard, mock and future GPIO.

Eager imports stay free of api/runtime so ``orng_led.config`` can load in a
clean process. Heavy symbols (dispatcher) resolve lazily via ``__getattr__``.
"""

from __future__ import annotations

from typing import Any

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
    legacy_source_id,
    resolve_source_id,
)
from orng_led.input.debounce import EdgeDebouncer
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
    "legacy_source_id",
    "resolve_source_id",
]

_LAZY = {
    "DispatchResult": ("orng_led.input.dispatcher", "DispatchResult"),
    "InputDispatcher": ("orng_led.input.dispatcher", "InputDispatcher"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr = target
    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, attr)
    globals()[name] = value
    return value
