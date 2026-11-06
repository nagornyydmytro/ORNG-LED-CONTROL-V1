"""Fake-evdev keyboard hotplug / disconnect / scene mapping (no hardware)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from orng_led.input.contract import InputEvent
from orng_led.input.linux_keyboard import (
    LinuxKeyboardAdapter,
    device_allowed,
    parse_allowlist,
    source_id_for_path,
)


@dataclass
class _FakeDispatcher:
    events: list[InputEvent] = field(default_factory=list)
    released: list[str] = field(default_factory=list)

    def dispatch(self, event: InputEvent) -> None:
        self.events.append(event)

    def force_release_source(self, source_id: str) -> None:
        self.released.append(source_id)


@dataclass
class _FakeRuntime:
    input_dispatcher: _FakeDispatcher = field(default_factory=_FakeDispatcher)
    scene_controls: Any = None


class _AsyncDevice:
    def __init__(self, path: str, queue: asyncio.Queue) -> None:
        self.path = path
        self._queue = queue

    async def async_read_one(self) -> dict[str, Any]:
        return await self._queue.get()

    def close(self) -> None:
        return None


def test_allowlist_and_source_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORNG_LINUX_KEYBOARD_ALLOW", "name:orng pad|vendor:1234")
    allow = parse_allowlist()
    assert device_allowed(
        {"path": "/dev/input/event2", "name": "ORNG Pad", "vendor": "0000", "product": "0000"},
        allow,
    )
    assert not device_allowed(
        {"path": "/dev/input/event3", "name": "Other", "vendor": "9999", "product": "0000"},
        allow,
    )
    assert source_id_for_path("/dev/input/event0") == "linux-keyboard:_dev_input_event0"


def test_hotplug_disconnect_and_scene_switch() -> None:
    async def _run() -> None:
        import os

        os.environ["ORNG_LINUX_KEYBOARD"] = "1"
        os.environ["ORNG_LINUX_KEYBOARD_POLL_S"] = "0.15"
        runtime = _FakeRuntime()
        from orng_led.scenes.models import SceneControls

        runtime.scene_controls = SceneControls()
        q1: asyncio.Queue = asyncio.Queue()
        devices = [{"path": "/dev/input/event1", "name": "KB1", "vendor": "1", "product": "1"}]

        adapter = LinuxKeyboardAdapter(runtime=runtime)  # type: ignore[arg-type]
        adapter.status.enabled = True
        adapter.status.supported = True
        adapter.list_devices_fn = lambda: list(devices)
        adapter.open_device_fn = lambda path: _AsyncDevice(path, q1)

        await adapter.start()
        await asyncio.sleep(0.2)
        assert adapter.status.connected
        assert adapter.status.count == 1
        sid = source_id_for_path("/dev/input/event1")

        await q1.put({"code": "KeyQ", "type": "keydown", "repeat": False})
        await asyncio.sleep(0.05)

        devices.clear()
        await asyncio.sleep(0.35)
        assert sid in runtime.input_dispatcher.released or adapter.status.count == 0

        q2: asyncio.Queue = asyncio.Queue()
        devices.append(
            {"path": "/dev/input/event2", "name": "KB2", "vendor": "2", "product": "2"}
        )
        adapter.open_device_fn = lambda path: _AsyncDevice(path, q2)
        await asyncio.sleep(0.35)

        adapter.notify_controls_changed()
        assert adapter.status.mapping_generation >= 1

        await adapter.stop()

    asyncio.run(_run())


def test_autorepeat_ignored() -> None:
    async def _run() -> None:
        import os

        os.environ["ORNG_LINUX_KEYBOARD"] = "1"
        os.environ["ORNG_LINUX_KEYBOARD_POLL_S"] = "0.15"
        runtime = _FakeRuntime()
        from orng_led.scenes.models import SceneControls

        runtime.scene_controls = SceneControls()
        q: asyncio.Queue = asyncio.Queue()
        adapter = LinuxKeyboardAdapter(runtime=runtime)  # type: ignore[arg-type]
        adapter.status.enabled = True
        adapter.status.supported = True
        adapter.list_devices_fn = lambda: [
            {"path": "/dev/input/event9", "name": "KB", "vendor": "1", "product": "1"}
        ]
        adapter.open_device_fn = lambda path: _AsyncDevice(path, q)
        await adapter.start()
        await asyncio.sleep(0.2)
        before = len(runtime.input_dispatcher.events)
        await q.put({"code": "KeyQ", "type": "keydown", "repeat": True})
        await asyncio.sleep(0.05)
        assert len(runtime.input_dispatcher.events) == before
        await adapter.stop()

    asyncio.run(_run())
