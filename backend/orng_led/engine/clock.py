"""Engine timing utilities."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeClock:
    """Monotonic virtual clock for deterministic tests and offline ticks."""

    _seconds: float = 0.0

    def time(self) -> float:
        return self._seconds

    def advance(self, delta_s: float) -> float:
        if delta_s < 0:
            raise ValueError(f"Cannot move FakeClock backwards (delta={delta_s}).")
        self._seconds += delta_s
        return self._seconds

    def set(self, seconds: float) -> float:
        if seconds < self._seconds:
            raise ValueError("FakeClock is monotonic; use a new instance to rewind.")
        self._seconds = seconds
        return self._seconds


FPS = 30
FRAME_DT = 1.0 / FPS
