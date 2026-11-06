"""HTTP/WebSocket API package.

Lazy exports avoid circular imports when ``orng_led.api.schemas`` is loaded
from the input dispatcher before routes/runtime finish initializing.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AppRuntime",
    "build_api_router",
    "build_ws_router",
]

_LAZY = {
    "AppRuntime": ("orng_led.api.runtime", "AppRuntime"),
    "build_api_router": ("orng_led.api.routes", "build_api_router"),
    "build_ws_router": ("orng_led.api.ws", "build_ws_router"),
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
