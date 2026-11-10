"""The Windows desk follows the executor socket and does not poll beside it."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from websockets.asyncio.server import serve

from orng_led.desktop import remote_desk


def _runtime() -> SimpleNamespace:
    return SimpleNamespace(
        config_root="x",
        _shutting_down=False,
        executor_online=None,
        executor_view=None,
    )


def test_socket_carries_the_snapshot_and_http_waits_until_it_drops(monkeypatch) -> None:
    http = {"n": 0, "inflight": 0, "max": 0}

    async def fetch() -> dict:
        http["inflight"] += 1
        http["max"] = max(http["max"], http["inflight"])
        await asyncio.sleep(0.02)
        http["inflight"] -= 1
        http["n"] += 1
        return {"online": False}

    monkeypatch.setattr(remote_desk, "desk_role", lambda _root: "raspberry")
    monkeypatch.setattr(remote_desk, "load_device_token", lambda: "token")
    monkeypatch.setattr(remote_desk, "fetch_executor_state", fetch)
    monkeypatch.setattr(remote_desk, "EXECUTOR_FALLBACK_S", 30.0)

    async def body() -> None:
        remote_desk._watch_owner = None
        remote_desk._fallback_busy = False
        started = asyncio.Event()
        release = asyncio.Event()
        connections = {"n": 0}

        async def handler(ws) -> None:
            connections["n"] += 1
            await ws.send(
                json.dumps(
                    {
                        "type": "admin_hello",
                        "device_id": "pi-1",
                        "payload": {
                            "online": True,
                            "device_online": True,
                            "last_state": {"blackout": True, "preset_id": "look-a"},
                        },
                    }
                )
            )
            await ws.send(
                json.dumps(
                    {
                        "type": "device_heartbeat",
                        "payload": {"blackout": False, "preset_id": "look-a"},
                    }
                )
            )
            await ws.send(json.dumps({"type": "device_offline", "payload": {"online": False}}))
            started.set()
            await release.wait()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            async def opener():
                from websockets.asyncio.client import connect

                return await connect(f"ws://127.0.0.1:{port}")

            monkeypatch.setattr(remote_desk, "open_executor_socket", opener)
            runtime = _runtime()
            first = asyncio.create_task(remote_desk.watch_executor(runtime))
            second = asyncio.create_task(remote_desk.watch_executor(runtime))
            await asyncio.wait_for(started.wait(), 2)
            await asyncio.sleep(0.05)
            assert runtime.executor_online is False
            assert runtime.executor_view["blackout"] is False
            assert runtime.executor_view["preset_id"] == "look-a"
            assert runtime.executor_view["installation_id"] == "pi-1"
            assert http["n"] == 0
            assert connections["n"] == 1
            assert second.done()
            release.set()
            await asyncio.sleep(0.3)
            assert http["n"] == 1
            assert http["max"] == 1
            first.cancel()
            with_exc = None
            try:
                await first
            except asyncio.CancelledError:
                with_exc = True
            assert with_exc is True

    asyncio.run(body())


def test_missing_token_does_not_call_cloud(monkeypatch) -> None:
    called = {"n": 0}

    async def fetch() -> dict:
        called["n"] += 1
        return {}

    async def opener():
        called["n"] += 1
        raise AssertionError("socket")

    monkeypatch.setattr(remote_desk, "desk_role", lambda _root: "raspberry")
    monkeypatch.setattr(remote_desk, "load_device_token", lambda: "")
    monkeypatch.setattr(remote_desk, "fetch_executor_state", fetch)
    monkeypatch.setattr(remote_desk, "open_executor_socket", opener)
    monkeypatch.setattr(remote_desk, "EXECUTOR_IDLE_S", 0.02)

    async def body() -> None:
        remote_desk._watch_owner = None
        runtime = _runtime()
        task = asyncio.create_task(remote_desk.watch_executor(runtime))
        await asyncio.sleep(0.08)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert called["n"] == 0
        assert runtime.executor_online is None

    asyncio.run(body())
