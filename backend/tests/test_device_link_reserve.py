"""The device socket is the live channel. HTTP link is only the limited reserve."""

from __future__ import annotations

import asyncio
import time

from orng_led.api.runtime import AppRuntime
from orng_led.cloud.agent import MOMENTARY_PRESS, CloudDeviceAgent
from orng_led.output.mock import MockTransport


class _Response:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _Client:
    bodies: list[dict] = []
    times: list[float] = []
    stop_after = 2

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def post(self, url: str, json: dict, headers: dict) -> _Response:
        del url, headers
        _Client.bodies.append(json)
        _Client.times.append(time.monotonic())
        return _Response({"commands": [], "relay": "database"})


def _runtime() -> AppRuntime:
    runtime = AppRuntime.create(autostart_loop=False)
    assert isinstance(runtime.output.transport, MockTransport)
    return runtime


def test_a_live_socket_does_not_poll_link(monkeypatch) -> None:
    agent = CloudDeviceAgent(_runtime())
    posts: list[str] = []

    async def session(url: str, token: str) -> None:
        del url, token
        await asyncio.sleep(0.05)
        agent._stop.set()

    async def link_once(token: str) -> None:
        posts.append(token)

    async def no_rotation() -> None:
        return None

    agent._session = session  # type: ignore[method-assign]
    agent._link_once = link_once  # type: ignore[method-assign]
    monkeypatch.setattr("orng_led.cloud.agent.load_device_token", lambda: "device-token")
    monkeypatch.setattr("orng_led.cloud.agent.cloud_device_url", lambda: "wss://device.example/ws")
    monkeypatch.setattr("orng_led.cloud.rotation.complete_pending_rotation", no_rotation)

    asyncio.run(agent._connect_loop(0))
    assert posts == []


def test_a_dropped_socket_posts_once_and_a_refusal_does_not_hold(monkeypatch) -> None:
    agent = CloudDeviceAgent(_runtime())
    calls = {"session": 0}

    async def session(url: str, token: str) -> None:
        del url, token
        calls["session"] += 1
        if calls["session"] == 1:
            raise RuntimeError("no close frame received or sent")
        agent._stop.set()

    async def link_once(token: str) -> None:
        calls["link"] = calls.get("link", 0) + 1
        agent._stop.set()

    async def no_rotation() -> None:
        return None

    agent._session = session  # type: ignore[method-assign]
    agent._link_once = link_once  # type: ignore[method-assign]
    monkeypatch.setattr("orng_led.cloud.agent.load_device_token", lambda: "device-token")
    monkeypatch.setattr("orng_led.cloud.agent.cloud_device_url", lambda: "wss://device.example/ws")
    monkeypatch.setattr("orng_led.cloud.rotation.complete_pending_rotation", no_rotation)
    asyncio.run(agent._connect_loop(0))
    assert calls["link"] == 1

    direct = CloudDeviceAgent(_runtime())
    _Client.bodies = []
    monkeypatch.setattr("orng_led.cloud.sync_service.cloud_http_base", lambda: "https://device.example")
    monkeypatch.setattr("httpx.AsyncClient", _Client)
    asyncio.run(direct._link_once("device-token"))
    assert _Client.bodies[-1]["wait_ms"] == 0

    refused = CloudDeviceAgent(_runtime())
    gaps_at: list[float] = []

    async def refused_session(url: str, token: str) -> None:
        del url, token
        raise RuntimeError("server rejected WebSocket connection: HTTP 500")

    async def paced_link(token: str) -> bool:
        del token
        gaps_at.append(time.monotonic())
        if len(gaps_at) >= 3:
            refused._stop.set()
        return True

    refused._session = refused_session  # type: ignore[method-assign]
    refused._link_once = paced_link  # type: ignore[method-assign]
    monkeypatch.setattr("orng_led.cloud.agent.LINK_INTERVAL_S", 0.12)
    monkeypatch.setattr("orng_led.cloud.agent.LINK_SOCKET_RETRY_S", 2.0)
    asyncio.run(refused._connect_loop(0))
    gaps = [b - a for a, b in zip(gaps_at, gaps_at[1:], strict=False)]
    assert len(gaps_at) >= 3
    assert min(gaps) >= 0.1


def test_a_momentary_press_is_not_deduplicated() -> None:
    runtime = _runtime()
    agent = CloudDeviceAgent(runtime)

    class Sink:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, text: str) -> None:
            self.sent.append(text)

    sink = Sink()

    async def press() -> None:
        await agent._handle_message(
            sink,
            {
                "type": "strobe_press",
                "message_id": "press-1",
                "payload": {"command_id": "press-1"},
            },
        )

    asyncio.run(press())
    asyncio.run(press())
    assert "press-1" not in agent._seen_commands
    assert "strobe_press" in MOMENTARY_PRESS
    assert len(sink.sent) == 2
