"""Phone/Windows → cloud → executor command path.

Real AppRuntime plus a recording cloud socket. Not a MagicMock dispatcher.
No UI changes, no venue UDP.
"""

from __future__ import annotations

import asyncio
import json
import math
import time

import pytest

from orng_led.cloud.budget import HEARTBEAT_INTERVAL_S, PREVIEW_INTERVAL_S

from orng_led.api.runtime import AppRuntime, CommandConflictError
from orng_led.cloud.agent import CloudDeviceAgent
from orng_led.desktop.output_role import local_artnet_allowed
from orng_led.input.contract import InputAction, InputEvent, InputSource
from orng_led.output.contract import OutputError
from orng_led.output.mock import MockTransport


class RecordingCloudSocket:
    """In-memory cloud transport. Never opens a network socket."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))

    def last_ack(self) -> dict:
        acks = [item for item in self.sent if item.get("type") == "ack"]
        assert acks, self.sent
        return acks[-1]["payload"]


def _runtime() -> AppRuntime:
    runtime = AppRuntime.create(autostart_loop=False)
    assert isinstance(runtime.output.transport, MockTransport)
    runtime.engine.set_blackout(False)
    return runtime


def _agent(runtime: AppRuntime) -> tuple[CloudDeviceAgent, RecordingCloudSocket]:
    agent = CloudDeviceAgent(runtime)
    return agent, RecordingCloudSocket()


def _send(
    agent: CloudDeviceAgent,
    ws: RecordingCloudSocket,
    msg_type: str,
    **payload: object,
) -> dict:
    command_id = str(payload.get("command_id") or msg_type)
    message = {"type": msg_type, "message_id": command_id, "payload": payload}

    async def run() -> None:
        await agent._handle_message(ws, message)

    asyncio.run(run())
    return ws.last_ack()


def test_auto_strobe_press_holds_over_the_director() -> None:
    runtime = _runtime()
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    agent, ws = _agent(runtime)
    ack = _send(
        agent,
        ws,
        "strobe_press",
        command_id="press-1",
        source="cloud:web:phone",
        hold_id="hold-1",
    )
    assert ack["status"] == "applied"
    assert runtime.engine.overlays.strobe_held is True
    assert agent._leases.current_token("cloud:web:phone", "strobe") == "hold-1"

    release = _send(
        agent,
        ws,
        "strobe_release",
        command_id="rel-1",
        source="cloud:web:phone",
        hold_id="hold-1",
    )
    assert release["status"] == "applied"
    assert runtime.engine.overlays.strobe_held is False
    assert agent._leases.current_token("cloud:web:phone", "strobe") is None


def test_auto_sweep_and_keyboard_are_rejected_without_a_lease() -> None:
    runtime = _runtime()
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    agent, ws = _agent(runtime)
    sweep = _send(
        agent,
        ws,
        "sweep_press",
        command_id="sw-1",
        source="cloud:desk:win",
        hold_id="sweep-1",
    )
    assert sweep["status"] == "applied"
    assert runtime.engine.overlays.sweep_held is True
    assert agent._leases.current_token("cloud:desk:win", "sweep") == "sweep-1"

    key = _send(
        agent,
        ws,
        "keyboard",
        command_id="key-1",
        source="cloud:desk:win",
        code="KeyQ",
        type="keydown",
        profile_id="ch57x",
    )
    assert key["status"] == "applied"
    assert runtime.engine.overlays.strobe_held is True

    vertical = _send(
        agent,
        ws,
        "keyboard",
        command_id="key-o",
        source="cloud:desk:win",
        code="KeyO",
        type="keydown",
        profile_id="ch57x",
    )
    assert vertical["status"] == "applied"
    assert runtime.engine.overlays.sweep_held is True

    up = _send(
        agent,
        ws,
        "keyboard",
        command_id="key-up",
        source="cloud:desk:win",
        code="KeyQ",
        type="keyup",
        profile_id="ch57x",
    )
    assert up["status"] == "applied"


def test_two_clients_share_a_hold_id_without_stealing() -> None:
    runtime = _runtime()
    runtime.apply_bpm_sync(enabled=True, scope="preset")
    agent, ws = _agent(runtime)
    _send(
        agent,
        ws,
        "strobe_press",
        command_id="a-old",
        source="cloud:web:a",
        hold_id="old-a",
    )
    first = _send(
        agent,
        ws,
        "strobe_press",
        command_id="a-press",
        source="cloud:web:a",
        hold_id="shared",
    )
    second = _send(
        agent,
        ws,
        "strobe_press",
        command_id="b-press",
        source="cloud:web:b",
        hold_id="shared",
    )
    assert first["status"] == "applied"
    assert second["status"] == "applied"
    assert runtime.engine.overlays.strobe_held is True
    assert agent._leases.current_token("cloud:web:a", "strobe") == "shared"
    assert agent._leases.current_token("cloud:web:b", "strobe") == "shared"
    assert runtime.input_dispatcher.holds.is_held_by("strobe", "cloud:web:a")
    assert runtime.input_dispatcher.holds.is_held_by("strobe", "cloud:web:b")

    late = _send(
        agent,
        ws,
        "strobe_release",
        command_id="a-late-old",
        source="cloud:web:a",
        hold_id="old-a",
    )
    assert late["status"] == "applied"
    assert agent._leases.current_token("cloud:web:a", "strobe") == "shared"
    assert agent._leases.current_token("cloud:web:b", "strobe") == "shared"
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.input_dispatcher.holds.is_held_by("strobe", "cloud:web:a")
    assert runtime.input_dispatcher.holds.is_held_by("strobe", "cloud:web:b")

    gone = _send(agent, ws, "release_source", command_id="a-gone", source="cloud:web:a")
    assert gone["status"] == "applied"
    assert agent._leases.current_token("cloud:web:a", "strobe") is None
    assert agent._leases.current_token("cloud:web:b", "strobe") == "shared"
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.input_dispatcher.holds.is_held_by("strobe", "cloud:web:b")

    runtime.apply_bpm_sync(enabled=True, scope="auto")
    assert runtime.engine.overlays.strobe_held is True
    during = _send(
        agent,
        ws,
        "strobe_press",
        command_id="b-during-auto",
        source="cloud:web:b",
        hold_id="during-auto",
    )
    assert during["status"] == "applied"
    assert runtime.engine.overlays.strobe_held is True
    assert agent._leases.current_token("cloud:web:b", "strobe") == "during-auto"
    expired = agent._leases.release_token("during-auto", "cloud:web:b", "strobe")
    assert expired is not None
    agent._release_cloud_lease(expired)
    assert agent._leases.current_token("cloud:web:b", "strobe") is None

    runtime.apply_bpm_sync(enabled=True, scope="preset")
    after = _send(
        agent,
        ws,
        "strobe_press",
        command_id="b-after-auto",
        source="cloud:web:b",
        hold_id="after-auto",
    )
    assert after["status"] == "applied"
    assert runtime.engine.overlays.strobe_held is True


def test_blackout_replay_returns_current_state_not_the_old_picture() -> None:
    runtime = _runtime()
    agent, ws = _agent(runtime)
    first = _send(agent, ws, "blackout", command_id="bo-a", source="cloud:web:a", enabled=False)
    assert first["status"] == "applied"
    assert first["idempotent_replay"] is False
    assert first["blackout"] is False
    assert runtime.engine.overlays.blackout is False

    other = _send(agent, ws, "blackout", command_id="bo-b", source="cloud:web:b", enabled=True)
    assert other["blackout"] is True
    assert runtime.engine.overlays.blackout is True

    replay = _send(agent, ws, "blackout", command_id="bo-a", source="cloud:web:a", enabled=False)
    assert replay["status"] == "applied"
    assert replay["idempotent_replay"] is True
    assert replay["blackout"] is True
    assert runtime.engine.overlays.blackout is True

    conflict = _send(agent, ws, "blackout", command_id="bo-a", source="cloud:web:a", enabled=True)
    assert conflict["status"] == "failed"
    assert conflict["error_code"] == "command_conflict"
    assert runtime.engine.overlays.blackout is True

    state, replayed = runtime.apply_blackout(False, client_command_id="bo-a")
    assert replayed is True
    assert state.engine.blackout is True
    with pytest.raises(CommandConflictError):
        runtime.apply_blackout(True, client_command_id="bo-a")


def test_local_macropad_survives_cloud_drop_and_windows_desk_cannot_open_artnet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    runtime.apply_bpm_sync(enabled=True, scope="preset")
    agent, _ws = _agent(runtime)
    agent._leases.arm("cloud:web:phone", "strobe", "cloud-hold")
    agent._drop_all_cloud_holds()
    assert agent._leases.current_token("cloud:web:phone", "strobe") is None

    local = runtime.input_dispatcher.dispatch(
        InputEvent(
            action=InputAction.STROBE_PRESS,
            source=InputSource.KEYBOARD,
            source_id="linux-keyboard:pad",
        )
    )
    assert local.accepted is True
    assert runtime.engine.overlays.strobe_held is True
    assert agent._leases.current_token("linux-keyboard:pad", "strobe") is None

    monkeypatch.setenv("ORNG_DESKTOP_OUTPUT", "raspberry")
    assert local_artnet_allowed() is False
    with pytest.raises(OutputError, match="Raspberry"):
        runtime.activate_artnet_network(confirmed=True, allow_real_udp=True)


def _rank(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def test_a_live_socket_acks_the_apply_without_waiting_for_telemetry() -> None:
    runtime = _runtime()
    agent, ws = _agent(runtime)
    agent._subscribers = 1

    async def run() -> list[float]:
        beat = asyncio.create_task(agent._heartbeat(ws))
        samples: list[float] = []
        try:
            for index in range(40):
                enabled = index % 2 == 0
                started = time.perf_counter()
                await agent._handle_message(
                    ws,
                    {
                        "type": "blackout",
                        "message_id": f"live-{index}",
                        "payload": {"command_id": f"live-{index}", "enabled": enabled},
                    },
                )
                samples.append(time.perf_counter() - started)
                ack = ws.last_ack()
                assert ack["status"] == "applied"
                assert ack["blackout"] is enabled
                assert runtime.engine.overlays.blackout is enabled
        finally:
            agent._stop.set()
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
        return samples

    samples = asyncio.run(run())
    heartbeats = [item for item in ws.sent if item.get("type") == "heartbeat"]
    p50 = _rank(samples, 0.50)
    p95 = _rank(samples, 0.95)
    slowest = max(samples)
    print(
        f"socket apply ACK n={len(samples)} "
        f"p50={p50 * 1000:.2f}ms p95={p95 * 1000:.2f}ms max={slowest * 1000:.2f}ms"
    )
    assert len(heartbeats) < 40
    assert p95 < HEARTBEAT_INTERVAL_S
    assert p95 < PREVIEW_INTERVAL_S
    assert p95 < 0.1
