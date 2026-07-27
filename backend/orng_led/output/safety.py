"""Held-control failsafe helpers (strobe release on disconnect/focus/visibility)."""

from __future__ import annotations

from enum import StrEnum

from orng_led.engine.engine import Engine


class FailsafeReason(StrEnum):
    RELEASE = "release"
    TIMEOUT = "timeout"
    DISCONNECT = "disconnect"
    FOCUS_LOSS = "focus_loss"
    VISIBILITY_HIDDEN = "visibility_hidden"
    SHUTDOWN = "shutdown"


def release_held_controls(engine: Engine, reason: FailsafeReason | str) -> dict[str, object]:
    """Clear press/hold overlays without stopping the preset clock."""
    was_held = engine.overlays.strobe_held
    engine.strobe_release()
    return {
        "reason": str(reason),
        "strobe_was_held": was_held,
        "strobe_held": engine.overlays.strobe_held,
        "preset_id": engine.active_preset_id,
        "preset_elapsed_s": engine.preset_elapsed_s,
    }
