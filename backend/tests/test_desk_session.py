"""Desktop control uses the same session socket as the web desk.

The fake server speaks admin_hello / command_result / ack. HTTP accepted is not
an executor ACK.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from orng_led.desktop import remote_desk
from orng_led.input.windows_media_keys import encoder_remote_action, encoder_remote_command

Script = Callable[[object, dict, asyncio.Lock], Awaitable[None]]


async def _with_server(
    script: Script,
    body: Callable[[list[dict], list[str]], Awaitable[None]],
) -> None:
    seen: list[dict] = []
    sources: list[str] = []

    async def handler(ws) -> None:
        source = f"cloud:desk:{uuid.uuid4().hex}"
        sources.append(source)
        send_lock = asyncio.Lock()
        async with send_lock:
            await ws.send(
                json.dumps(
                    {
                        "type": "admin_hello",
                        "payload": {"source": source, "online": True},
                    }
                )
            )

        async def handle(msg: dict) -> None:
            seen.append(msg)
            await script(ws, msg, send_lock)

        async for raw in ws:
            asyncio.create_task(handle(json.loads(raw)))

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        async def opener():
            return await connect(f"ws://127.0.0.1:{port}/device/executor/session")

        remote_desk._links.clear()
        remote_desk.open_executor_socket = opener
        try:
            await body(seen, sources)
        finally:
            for link in list(remote_desk._links.values()):
                await link.close()
            remote_desk._links.clear()
            remote_desk.APPLY_WAIT_S = 8.0


def test_two_windows_get_two_server_sources_and_ack_is_not_accepted() -> None:
    async def script(ws, msg: dict, send_lock: asyncio.Lock) -> None:
        cid = msg["payload"]["command_id"]
        async with send_lock:
            await ws.send(
                json.dumps(
                    {
                        "type": "command_result",
                        "payload": {"command_id": cid, "status": "accepted", "ok": True},
                    }
                )
            )
            if msg["type"] == "blackout":
                await ws.send(
                    json.dumps(
                        {
                            "type": "ack",
                            "payload": {"command_id": cid, "status": "applied"},
                        }
                    )
                )

    async def body(seen: list[dict], sources: list[str]) -> None:
        first = await remote_desk.forward_operator_command(
            "blackout",
            {"enabled": True, "source": "cloud:desk:tab-a", "command_id": "black-a"},
        )
        second = await remote_desk.forward_operator_command(
            "blackout",
            {"enabled": False, "source": "cloud:desk:tab-b", "command_id": "black-b"},
        )
        assert first["status"] == "applied"
        assert first["ok"] is True
        assert second["status"] == "applied"
        assert second["command_id"] == "black-b"
        assert sources[0] != sources[1]
        assert "source" not in seen[0]["payload"]

    asyncio.run(_with_server(script, body))


def test_release_is_not_queued_behind_press_ack_and_retry_is_one_send() -> None:
    async def script(ws, msg: dict, send_lock: asyncio.Lock) -> None:
        cid = msg["payload"]["command_id"]
        if msg["type"] == "strobe_press":
            await asyncio.sleep(0.25)
        async with send_lock:
            await ws.send(
                json.dumps(
                    {
                        "type": "command_result",
                        "payload": {"command_id": cid, "status": "accepted", "ok": True},
                    }
                )
            )

    async def body(seen: list[dict], _sources: list[str]) -> None:
        press = asyncio.create_task(
            remote_desk.forward_operator_command(
                "strobe_press",
                {
                    "source": "cloud:desk:tab-a",
                    "command_id": "press-1",
                    "hold_id": "hold-1",
                },
            )
        )
        await asyncio.sleep(0.05)
        release = await remote_desk.forward_operator_command(
            "strobe_release",
            {
                "source": "cloud:desk:tab-a",
                "command_id": "release-1",
                "hold_id": "hold-1",
            },
        )
        assert press.done() is False
        assert release["status"] == "accepted"
        assert release["ok"] is False
        press_result = await press
        assert press_result["status"] == "accepted"
        kinds = [item["type"] for item in seen]
        assert kinds.index("strobe_release") > kinds.index("strobe_press")

        first = asyncio.create_task(
            remote_desk.forward_operator_command(
                "strobe_press",
                {
                    "source": "cloud:desk:tab-a",
                    "command_id": "press-2",
                    "hold_id": "hold-2",
                },
            )
        )
        await asyncio.sleep(0.05)
        duplicate = await remote_desk.forward_operator_command(
            "strobe_press",
            {
                "source": "cloud:desk:tab-a",
                "command_id": "press-2",
                "hold_id": "hold-2",
            },
        )
        await first
        sent = [item for item in seen if item["payload"]["command_id"] == "press-2"]
        assert len(sent) == 1
        assert duplicate["command_id"] == "press-2"

    asyncio.run(_with_server(script, body))


def test_silence_after_accept_is_unknown_and_reconnect_does_not_replay() -> None:
    async def script(ws, msg: dict, send_lock: asyncio.Lock) -> None:
        cid = msg["payload"]["command_id"]
        async with send_lock:
            await ws.send(
                json.dumps(
                    {
                        "type": "command_result",
                        "payload": {"command_id": cid, "status": "accepted", "ok": True},
                    }
                )
            )
        if msg["type"] == "select_preset":
            await ws.close()

    async def body(seen: list[dict], _sources: list[str]) -> None:
        remote_desk.APPLY_WAIT_S = 0.2
        unknown = await remote_desk.forward_operator_command(
            "blackout",
            {"enabled": True, "source": "cloud:desk:tab-a", "command_id": "slow-1"},
        )
        assert unknown["status"] == "unknown"
        assert unknown["ok"] is False
        await remote_desk.forward_operator_command(
            "select_preset",
            {"preset_id": "P01", "source": "cloud:desk:tab-a", "command_id": "drop-1"},
        )
        await remote_desk.forward_operator_command(
            "blackout",
            {"enabled": True, "source": "cloud:desk:tab-a", "command_id": "next-1"},
        )
        replayed = [
            item
            for item in seen
            if item["type"] == "select_preset" and item["payload"]["command_id"] == "drop-1"
        ]
        assert len(replayed) == 1

    asyncio.run(_with_server(script, body))


def test_unsupported_ack_is_not_applied() -> None:
    async def script(ws, msg: dict, send_lock: asyncio.Lock) -> None:
        cid = msg["payload"]["command_id"]
        async with send_lock:
            await ws.send(
                json.dumps(
                    {
                        "type": "ack",
                        "payload": {"command_id": cid, "status": "unsupported"},
                    }
                )
            )

    async def body(_seen: list[dict], _sources: list[str]) -> None:
        result = await remote_desk.forward_operator_command(
            "seek_episode",
            {"episode_index": 1, "source": "cloud:desk:tab-a", "command_id": "old-runtime"},
        )
        assert result["status"] == "unsupported"
        assert result["ok"] is False

    asyncio.run(_with_server(script, body))


def test_keepalive_uses_the_open_hold() -> None:
    async def script(ws, msg: dict, send_lock: asyncio.Lock) -> None:
        cid = msg["payload"]["command_id"]
        async with send_lock:
            await ws.send(
                json.dumps(
                    {
                        "type": "ack",
                        "payload": {"command_id": cid, "status": "applied"},
                    }
                )
            )

    async def body(seen: list[dict], _sources: list[str]) -> None:
        await remote_desk.forward_operator_command(
            "strobe_press",
            {"source": "cloud:desk:tab-a", "command_id": "press-lease", "hold_id": "hold-lease"},
        )
        await remote_desk.forward_operator_command(
            "hold_keepalive",
            {
                "source": "cloud:desk:tab-a",
                "command_id": "keep-lease",
                "hold_id": "hold-lease",
            },
        )
        press = next(item for item in seen if item["type"] == "strobe_press")
        keep = next(item for item in seen if item["type"] == "hold_keepalive")
        assert press["payload"]["hold_id"] == "hold-lease"
        assert keep["payload"]["hold_id"] == "hold-lease"

    asyncio.run(_with_server(script, body))


def test_windows_encoder_is_its_own_source() -> None:
    command, payload = encoder_remote_command("speed", 0.25)
    assert command == "speed"
    assert payload["source"] == "cloud:desk:windows-encoder"
    episode, episode_payload = encoder_remote_command("episode", 1)
    assert episode == "episode_nudge"
    assert episode_payload["source"] == "cloud:desk:windows-encoder"
    assert payload["command_id"] != episode_payload["command_id"]
    remote = encoder_remote_action("raspberry", "speed", 0.25)
    assert remote is not None
    assert remote[0] == "speed"
    assert remote[1]["source"] == "cloud:desk:windows-encoder"
    assert encoder_remote_action("local", "episode", 1) is None
    assert encoder_remote_action("windows", "speed", -0.25) is None
