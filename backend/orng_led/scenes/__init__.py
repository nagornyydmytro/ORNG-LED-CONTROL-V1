"""Multi-scene discovery, validation, and loading.

Eager imports stay limited to models/store so config loading does not pull
the API/runtime stack. Prefer ``from orng_led.scenes.models import …`` when
importing from config.io to keep the dependency edge explicit.
"""

from __future__ import annotations

from typing import Any

from orng_led.scenes.models import (
    KeyboardBinding,
    RuntimeState,
    SceneBundle,
    SceneControls,
    SceneManifest,
    SceneSummary,
)
from orng_led.scenes.store import SceneStore, is_safe_scene_id, is_scene_directory

__all__ = [
    "KeyboardBinding",
    "RuntimeState",
    "SceneBundle",
    "SceneControls",
    "SceneManifest",
    "SceneStore",
    "SceneSummary",
    "is_safe_scene_id",
    "is_scene_directory",
]


def __getattr__(name: str) -> Any:
    # Keep package attribute access stable for callers that expect store helpers.
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
