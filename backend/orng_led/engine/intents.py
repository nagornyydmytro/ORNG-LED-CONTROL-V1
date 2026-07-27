"""Semantic lighting intents used by the renderer (not raw DMX)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rgbw:
    r: float = 0.0
    g: float = 0.0
    b: float = 0.0
    w: float = 0.0

    def scaled(self, intensity: float) -> Rgbw:
        intensity = max(0.0, min(1.0, intensity))
        return Rgbw(
            r=max(0.0, min(1.0, self.r * intensity)),
            g=max(0.0, min(1.0, self.g * intensity)),
            b=max(0.0, min(1.0, self.b * intensity)),
            w=max(0.0, min(1.0, self.w * intensity)),
        )


@dataclass(frozen=True)
class ParIntent:
    color: Rgbw = field(default_factory=Rgbw)
    intensity: float = 1.0
    strobe: float = 0.0


@dataclass(frozen=True)
class BarIntent:
    segments: tuple[float, ...] = (0.0,) * 8
    dimmer: float = 1.0
    strobe: float = 0.0


@dataclass(frozen=True)
class BeamIntent:
    pan: float = 0.5
    tilt: float = 0.5
    dimmer: float = 0.0
    color: float = 0.0
    shutter_open: bool = True
    strobe: float = 0.0


FixtureIntent = ParIntent | BarIntent | BeamIntent


@dataclass
class StageIntent:
    """Desired semantic look for one engine tick."""

    fixtures: dict[str, FixtureIntent] = field(default_factory=dict)
