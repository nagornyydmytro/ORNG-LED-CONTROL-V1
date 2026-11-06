"""Musical period of one action on the shared beat clock.

Old cards have no ``cycle_beats``. The adaptation below is a pure function of
the authored effect and speed at the 120 BPM reference. It does not read the
live tempo, and it is not written back into a scene file.
"""

from __future__ import annotations

import math

from orng_led.audio.bpm import REFERENCE_BPM
from orng_led.engine.timing import EFFECT_RATE_SCALE, effect_rate_hz
from orng_led.presets.models import CYCLE_BEATS, LookAction

# Same inputs always land on the same period. Ties take the shorter cycle.
def adapted_cycle_beats(effect: str, speed: float) -> int | None:
    """Explicit period for a card written before musical cycles existed."""
    if EFFECT_RATE_SCALE.get(effect, 1.0) <= 0.0:
        return None
    hz = effect_rate_hz(speed, effect)
    if hz <= 1e-6:
        return None
    beats = (REFERENCE_BPM / 60.0) / hz
    return min(CYCLE_BEATS, key=lambda choice: (abs(math.log(choice / beats)), choice))


def action_cycle(action: LookAction) -> int | None:
    """Authored period, or the frozen adaptation. Static stays static."""
    if action.effect == "static":
        return None
    if action.cycle_beats is not None:
        return int(action.cycle_beats)
    return adapted_cycle_beats(action.effect, action.speed)


def musical_turns(beats: float, cycle_beats: float) -> float:
    """Phase in turns. A 4-beat cycle is a quarter turn after one beat.

    Auto may pass a half-beat period. Preset documents stay on 1/2/4/8.
    """
    period = float(cycle_beats)
    if period <= 0.0:
        return 0.0
    return float(beats) / period
