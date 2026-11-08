"""One live-preview slot. A slow sender keeps the newest picture."""

from __future__ import annotations


class PreviewOutbox:
    """Replaces a waiting packet. It does not grow a queue."""

    def __init__(self) -> None:
        self._latest: dict[str, object] | None = None
        self._busy = False
        self.sent = 0
        self.replaced = 0
        self.bytes_sent = 0

    def offer(self, packet: dict[str, object]) -> None:
        if self._latest is not None or self._busy:
            self.replaced += 1
        self._latest = packet

    def start(self) -> dict[str, object] | None:
        if self._busy or self._latest is None:
            return None
        packet = self._latest
        self._latest = None
        self._busy = True
        return packet

    def finish(self, size: int) -> dict[str, object] | None:
        self._busy = False
        self.sent += 1
        self.bytes_sent += max(0, int(size))
        return self.start()
