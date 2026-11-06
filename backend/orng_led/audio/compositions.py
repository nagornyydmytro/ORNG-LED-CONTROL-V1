"""Auto looks. Spatial painting lives in ``library``; this module keeps the old names.

Bar segments are on or off. The venue bars store a palette per segment, so a
fractional segment level would still decode as fully lit. Brightness of a bar
is its dimmer. Beam brightness stays binary in the renderer.
"""

from __future__ import annotations

from dataclasses import dataclass

from orng_led.audio.library import LOOKS, look_name

__all__ = ["LOOKS", "PairLook", "look_name", "pair_look"]


@dataclass(frozen=True)
class PairLook:
    include: bool
    intensity: float
    segments: tuple[float, ...]
    whole: bool


def _solid(count: int) -> tuple[float, ...]:
    return tuple(1.0 for _ in range(max(1, count)))


def pair_look(
    name: str,
    *,
    section: str,
    phase: float,
    amount: float,
    cap: float,
    accent: bool,
    rank: int,
    ranks: int,
    count: int,
    honor_look: bool = False,
) -> PairLook:
    """Legacy pair helper kept for the planned-look honour test.

    New Auto painting does not require a mirror pair. A planned ``groups``
    look still keeps its rank gate when the section tracker moves.
    """
    del accent
    level = min(cap, max(0.0, amount))
    if name == "groups":
        if ranks >= 2 and rank != (0 if phase < 0.5 else ranks - 1):
            if honor_look or section != "break":
                return PairLook(False, 0.0, _solid(count), True)
        if not honor_look and section == "break":
            return PairLook(True, level, _solid(count), True)
        return PairLook(True, level, _solid(count), True)
    return PairLook(True, level, _solid(count), True)
