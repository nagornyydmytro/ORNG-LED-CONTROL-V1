"""Finite cloud-hold leases checked on the device itself.

Linux pad sources are never tracked. A Worker outage cannot keep a cloud
effect alive, and an older hold id cannot cancel a newer one.

Each lease belongs to one source, one effect and one press token. The same
``hold_id`` from two operators is two records.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

CLOUD_HOLD_LEASE_S = 8.0


@dataclass
class CloudLease:
    source: str
    effect: str
    token: str
    until: float
    profile_id: str = ""


def _item_key(source: str, effect: str, token: str) -> tuple[str, str, str]:
    return (source, effect, token)


class CloudHoldLeases:
    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._items: dict[tuple[str, str, str], CloudLease] = {}
        self._current: dict[tuple[str, str], str] = {}

    def arm(
        self,
        source: str,
        effect: str,
        token: str,
        *,
        profile_id: str = "",
    ) -> None:
        if not str(source).startswith("cloud:"):
            return
        if not token:
            return
        previous = self._current.get((source, effect))
        if previous and previous != token:
            self._items.pop(_item_key(source, effect, previous), None)
        self._items[_item_key(source, effect, token)] = CloudLease(
            source=source,
            effect=effect,
            token=token,
            until=self._clock() + CLOUD_HOLD_LEASE_S,
            profile_id=profile_id,
        )
        self._current[(source, effect)] = token

    def _lookup(
        self,
        token: str,
        source: str | None = None,
        effect: str | None = None,
    ) -> CloudLease | None:
        if source and effect:
            return self._items.get(_item_key(source, effect, token))
        matches = [
            item
            for item in self._items.values()
            if item.token == token
            and (source is None or item.source == source)
            and (effect is None or item.effect == effect)
        ]
        if len(matches) != 1:
            return None
        return matches[0]

    def refresh(
        self,
        token: str,
        source: str | None = None,
        effect: str | None = None,
    ) -> bool:
        item = self._lookup(token, source, effect)
        if item is None:
            return False
        if self._current.get((item.source, item.effect)) != token:
            return False
        item.until = self._clock() + CLOUD_HOLD_LEASE_S
        return True

    def refresh_effect(self, source: str, effect: str) -> bool:
        token = self._current.get((source, effect))
        if not token:
            return False
        return self.refresh(token, source, effect)

    def current_token(self, source: str, effect: str) -> str | None:
        return self._current.get((source, effect))

    def owns(self, source: str, effect: str, token: str) -> bool:
        current = self._current.get((source, effect))
        if current is None:
            return True
        return current == token

    def release_token(
        self,
        token: str,
        source: str | None = None,
        effect: str | None = None,
    ) -> CloudLease | None:
        item = self._lookup(token, source, effect)
        if item is None:
            return None
        self._items.pop(_item_key(item.source, item.effect, item.token), None)
        if self._current.get((item.source, item.effect)) == item.token:
            self._current.pop((item.source, item.effect), None)
        return item

    def take_source(self, source: str) -> list[CloudLease]:
        tokens = [
            (item.token, item.effect) for item in self._items.values() if item.source == source
        ]
        taken: list[CloudLease] = []
        for token, effect in tokens:
            item = self.release_token(token, source, effect)
            if item is not None:
                taken.append(item)
        return taken

    def take_prefix(self, source: str, prefix: str) -> list[CloudLease]:
        tokens = [
            (item.token, item.effect)
            for item in self._items.values()
            if item.source == source and item.effect.startswith(prefix)
        ]
        taken: list[CloudLease] = []
        for token, effect in tokens:
            item = self.release_token(token, source, effect)
            if item is not None:
                taken.append(item)
        return taken

    def expired(self) -> list[CloudLease]:
        now = self._clock()
        due = [
            (item.token, item.source, item.effect)
            for item in self._items.values()
            if item.until <= now
        ]
        taken: list[CloudLease] = []
        for token, source, effect in due:
            item = self.release_token(token, source, effect)
            if item is not None:
                taken.append(item)
        return taken

    def take_all(self) -> list[CloudLease]:
        items = list(self._items.values())
        self._items.clear()
        self._current.clear()
        return items
