"""Output transport contract."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class TransportKind(StrEnum):
    MOCK = "mock"
    ARTNET = "artnet"


class OutputError(RuntimeError):
    """Raised for unsafe or invalid output operations."""


class OutputTransport(Protocol):
    """Common adapter for DMX frame delivery."""

    @property
    def kind(self) -> TransportKind: ...

    @property
    def last_frame(self) -> list[int] | None: ...

    def send_frame(self, frame: list[int]) -> None: ...

    def close(self) -> None: ...
