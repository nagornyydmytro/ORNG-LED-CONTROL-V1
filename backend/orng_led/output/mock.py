"""Mock DMX output transport (HOME default)."""

from __future__ import annotations

from dataclasses import dataclass, field

from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.output.contract import OutputError, TransportKind


@dataclass
class MockTransport:
    """Records frames in memory; never touches the network."""

    frames: list[list[int]] = field(default_factory=list)
    closed: bool = False

    @property
    def kind(self) -> TransportKind:
        return TransportKind.MOCK

    @property
    def last_frame(self) -> list[int] | None:
        return self.frames[-1] if self.frames else None

    def send_frame(self, frame: list[int]) -> None:
        if self.closed:
            raise OutputError("MockTransport is closed")
        if len(frame) != DMX_UNIVERSE_SIZE:
            raise OutputError(f"Frame length must be {DMX_UNIVERSE_SIZE}")
        if any((not isinstance(v, int)) or v < 0 or v > 255 for v in frame):
            raise OutputError("Frame values must be integers in 0..255")
        self.frames.append(list(frame))

    def close(self) -> None:
        self.closed = True
