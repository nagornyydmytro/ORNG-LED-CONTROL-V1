"""A running renderer publishes a new sequence even when the effect name stays."""

from __future__ import annotations

import asyncio
import json

from orng_led.api.runtime import AppRuntime
from orng_led.cloud.agent import CloudDeviceAgent
from orng_led.cloud.budget import preview_key, should_publish_preview


class _Socket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))


def test_ticks_change_sequence_and_the_agent_still_publishes() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.tick()
    first = runtime.desk_preview()
    assert first is not None
    repeated = runtime.desk_preview()
    assert repeated is not None
    assert repeated["sequence"] == first["sequence"]
    assert not should_publish_preview(1, preview_key(first), repeated)
    runtime.tick()
    second = runtime.desk_preview()
    assert second is not None
    assert first["look"] == second["look"]
    assert first["program"] == second["program"]
    assert first["preset_id"] == second["preset_id"]
    assert second["sequence"] == first["sequence"] + 1
    assert should_publish_preview(1, None, first)
    assert should_publish_preview(1, preview_key(first), second)

    agent = CloudDeviceAgent(runtime)
    agent._subscribers = 1
    socket = _Socket()

    async def publish(packet: dict) -> None:
        if should_publish_preview(agent._subscribers, agent._preview_key, packet):
            agent._preview_key = preview_key(packet)
            agent._preview.offer(packet)
            await agent._send_preview(socket)

    async def run() -> None:
        await publish(first)
        await publish(repeated)
        await publish(second)

    asyncio.run(run())
    previews = [item["payload"] for item in socket.sent if item.get("type") == "preview"]
    assert [item["sequence"] for item in previews] == [first["sequence"], second["sequence"]]
    assert previews[0]["look"] == previews[1]["look"]
    assert "stage" not in previews[0]
