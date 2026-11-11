"""One live-preview slot. A slow sender keeps the newest picture."""

from __future__ import annotations


class PreviewOutbox:
    """Replaces a waiting packet. It does not grow a queue.

    ``start`` returns the packet and the epoch that owns the slot. A cancelled
    send must ``release`` that same epoch. A newer connection's ``drop`` moves
    the epoch, so the old sender cannot clear the new slot.
    """

    def __init__(self) -> None:
        self._latest: dict[str, object] | None = None
        self._busy = False
        self._epoch = 0
        self.sent = 0
        self.replaced = 0
        self.bytes_sent = 0

    def offer(self, packet: dict[str, object]) -> None:
        if self._latest is not None or self._busy:
            self.replaced += 1
        self._latest = packet

    def start(self) -> tuple[dict[str, object], int] | None:
        if self._busy or self._latest is None:
            return None
        packet = self._latest
        self._latest = None
        self._busy = True
        return packet, self._epoch

    def finish(self, size: int, epoch: int) -> tuple[dict[str, object], int] | None:
        if not self._busy or epoch != self._epoch:
            return None
        self._busy = False
        self.sent += 1
        self.bytes_sent += max(0, int(size))
        return self.start()

    def release(self, epoch: int) -> None:
        """The send that owns ``epoch`` was cancelled. A newer slot stays busy."""
        if not self._busy or epoch != self._epoch:
            return
        self._busy = False
        self._epoch += 1

    def drop(self, epoch: int | None = None) -> None:
        """This connection is gone. A newer attempt keeps its slot."""
        if not self._busy:
            return
        if epoch is not None and epoch != self._epoch:
            return
        self._busy = False
        self._epoch += 1
