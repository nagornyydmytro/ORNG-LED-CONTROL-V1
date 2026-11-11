"""A cancelled preview send must not stick the outbox for the next socket.

The sender is CloudDeviceAgent._preview_loop / _send_preview. The outbox
container alone is not this test.
"""

from __future__ import annotations

import asyncio
import json

from orng_led.api.runtime import AppRuntime
from orng_led.cloud.agent import CloudDeviceAgent
from orng_led.output.mock import MockTransport


class _BlockingSocket:
    """Blocks inside send, then cancels that sender before the send timeout."""

    def __init__(self) -> None:
        self.task: asyncio.Task[None] | None = None
        self.entered = asyncio.Event()

    async def send(self, text: str) -> None:
        del text
        self.entered.set()
        assert self.task is not None
        self.task.cancel()
        await asyncio.Event().wait()


class _RecordingSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.got = asyncio.Event()

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))
        self.got.set()


def test_cancelled_send_lets_the_next_connection_deliver_a_new_frame() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    assert isinstance(runtime.output.transport, MockTransport)
    runtime.engine.set_blackout(True)
    runtime.tick(dt_s=1.0 / 30.0)
    held = runtime._desk_sequence
    assert held >= 1
    agent = CloudDeviceAgent(runtime)
    agent._subscribers = 1

    async def run() -> None:
        blocked = _BlockingSocket()
        first = asyncio.create_task(agent._preview_loop(blocked))
        blocked.task = first
        await asyncio.wait_for(blocked.entered.wait(), timeout=4)
        first.cancel()
        agent._preview.drop()
        try:
            await first
        except asyncio.CancelledError:
            pass
        assert agent._preview.sent == 0
        assert agent._preview._busy is False
        runtime.tick(dt_s=1.0 / 30.0)
        assert runtime._desk_sequence == held + 1
        second_socket = _RecordingSocket()
        second = asyncio.create_task(agent._preview_loop(second_socket))
        try:
            await asyncio.wait_for(second_socket.got.wait(), timeout=4)
        finally:
            second.cancel()
            agent._preview.drop()
            try:
                await second
            except asyncio.CancelledError:
                pass
        assert second_socket.sent
        payload = second_socket.sent[0]["payload"]
        assert second_socket.sent[0]["type"] == "preview"
        assert payload["sequence"] == runtime._desk_sequence
        assert payload["session"] == runtime._desk_session
        assert isinstance(payload["age_ms"], (int, float))

    asyncio.run(run())
