"""Raw DMX tester session for setup (Mock-only, always starts/ends at zero)."""

from __future__ import annotations

from dataclasses import dataclass, field

from orng_led.config.schema import DMX_CHANNEL_MAX, DMX_CHANNEL_MIN, DMX_UNIVERSE_SIZE
from orng_led.engine.frame import empty_frame


@dataclass
class RawTesterSession:
    active: bool = False
    frame: list[int] = field(default_factory=empty_frame)

    def enter(self) -> list[int]:
        self.active = True
        self.frame = empty_frame()
        return list(self.frame)

    def exit(self) -> list[int]:
        self.frame = empty_frame()
        self.active = False
        return list(self.frame)

    def blackout(self) -> list[int]:
        self.frame = empty_frame()
        return list(self.frame)

    def set_channel(self, channel: int, value: int) -> list[int]:
        if not self.active:
            raise RuntimeError("Raw tester is not active")
        if channel < DMX_CHANNEL_MIN or channel > DMX_CHANNEL_MAX:
            raise ValueError(f"Channel must be {DMX_CHANNEL_MIN}..{DMX_CHANNEL_MAX}")
        if value < 0 or value > 255:
            raise ValueError("DMX value must be 0..255")
        self.frame[channel - 1] = value
        return list(self.frame)

    def set_channels(self, updates: dict[int, int]) -> list[int]:
        for channel, value in updates.items():
            self.set_channel(int(channel), int(value))
        return list(self.frame)

    @property
    def nonzero_channels(self) -> int:
        return sum(1 for value in self.frame if value > 0)

    def as_dict(self) -> dict:
        return {
            "active": self.active,
            "nonzero_channels": self.nonzero_channels,
            "frame": list(self.frame) if self.active else None,
            "universe_size": DMX_UNIVERSE_SIZE,
        }
