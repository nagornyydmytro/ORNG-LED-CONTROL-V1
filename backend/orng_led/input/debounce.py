"""Debounce / edge-filter abstraction for physical-style inputs."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EdgeDebouncer:
    """Suppress chatter and keyboard auto-repeat for edge-triggered actions.

    Held controls (Strobe) accept one press until a matching release. Repeated
    press events while held are ignored so key-repeat cannot leave a stuck
    logical hold beyond the real release.
    """

    min_interval_s: float = 0.05
    _last_accept: dict[str, float] = field(default_factory=dict)
    _held: set[str] = field(default_factory=set)

    def clear(self) -> None:
        self._last_accept.clear()
        self._held.clear()

    def accept_pulse(self, key: str, now: float) -> bool:
        last = self._last_accept.get(key)
        if last is not None and now - last < self.min_interval_s:
            return False
        self._last_accept[key] = now
        return True

    def accept_press(self, key: str, now: float) -> bool:
        if key in self._held:
            # Auto-repeat / bounce while already held.
            return False
        last = self._last_accept.get(f"{key}:press")
        if last is not None and now - last < self.min_interval_s:
            return False
        self._held.add(key)
        self._last_accept[f"{key}:press"] = now
        return True

    def accept_release(self, key: str, now: float) -> bool:
        if key not in self._held:
            # Spurious release / duplicate release — ignore, do not stick.
            return False
        self._held.discard(key)
        self._last_accept[f"{key}:release"] = now
        return True

    def is_held(self, key: str) -> bool:
        return key in self._held
