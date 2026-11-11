import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from orng_led.cloud.agent import CloudDeviceAgent
from orng_led.cloud.hold_lease import CloudHoldLeases
from orng_led.input.dispatcher import HoldRegistry


def test_cloud_lease_expires_on_the_device_clock() -> None:
    now = {"t": 10.0}
    leases = CloudHoldLeases(clock=lambda: now["t"])
    leases.arm("cloud:web:tab", "strobe", "hold-1")
    now["t"] = 17.9
    assert leases.expired() == []
    assert leases.refresh("hold-1") is True
    now["t"] = 25.89
    assert leases.expired() == []
    now["t"] = 25.9
    expired = leases.expired()
    assert [item.effect for item in expired] == ["strobe"]
    assert leases.expired() == []


def test_linux_pad_is_not_a_cloud_lease() -> None:
    now = {"t": 0.0}
    leases = CloudHoldLeases(clock=lambda: now["t"])
    leases.arm("linux-keyboard:pad", "strobe", "local")
    now["t"] = 100.0
    assert leases.expired() == []
    assert leases.current_token("linux-keyboard:pad", "strobe") is None


def test_old_hold_id_does_not_cancel_the_new_one() -> None:
    registry = HoldRegistry()
    assert registry.add("strobe", "cloud:web:tab", "old") is True
    assert registry.add("strobe", "cloud:web:tab", "new") is False
    assert registry.remove("strobe", "cloud:web:tab", "old") is None
    assert registry.is_held_by("strobe", "cloud:web:tab") is True
    assert registry.remove("strobe", "cloud:web:tab", "new") is True
    assert registry.is_held_by("strobe", "cloud:web:tab") is False


def test_agent_keepalive_extends_and_disconnect_releases_only_cloud() -> None:
    runtime = MagicMock()
    runtime.input_dispatcher.dispatch = MagicMock()
    agent = CloudDeviceAgent(runtime)
    now = {"t": 0.0}
    agent._leases = CloudHoldLeases(clock=lambda: now["t"])

    async def _run() -> None:
        ws = AsyncMock()
        await agent._handle_message(
            ws,
            {
                "type": "strobe_press",
                "message_id": "m1",
                "payload": {
                    "command_id": "c1",
                    "source": "cloud:web:tab",
                    "hold_id": "h1",
                },
            },
        )
        ack = json.loads(ws.send.await_args.args[0])
        assert ack["payload"]["status"] == "applied"
        now["t"] = 7.0
        await agent._handle_message(
            ws,
            {
                "type": "hold_keepalive",
                "message_id": "m2",
                "payload": {"command_id": "c2", "hold_id": "h1", "source": "cloud:web:tab"},
            },
        )
        now["t"] = 14.0
        assert agent._leases.expired() == []
        agent._drop_all_cloud_holds()
        assert agent._leases.current_token("cloud:web:tab", "strobe") is None
        released = runtime.input_dispatcher.dispatch.call_args_list[-1].args[0]
        assert released.action.value == "strobe_release"
        assert released.hold_token == "h1"

    asyncio.run(_run())


def test_release_uses_the_hold_id_not_the_command_id() -> None:
    runtime = MagicMock()
    runtime.input_dispatcher.dispatch = MagicMock()
    agent = CloudDeviceAgent(runtime)

    async def _run() -> None:
        ws = AsyncMock()
        await agent._handle_message(
            ws,
            {
                "type": "strobe_press",
                "message_id": "m1",
                "payload": {
                    "command_id": "c1",
                    "source": "cloud:web:tab",
                    "hold_id": "press-token",
                },
            },
        )
        await agent._handle_message(
            ws,
            {
                "type": "strobe_release",
                "message_id": "m2",
                "payload": {
                    "command_id": "c2",
                    "source": "cloud:web:tab",
                    "hold_id": "other-token",
                },
            },
        )
        assert agent._leases.current_token("cloud:web:tab", "strobe") == "press-token"
        await agent._handle_message(
            ws,
            {
                "type": "strobe_release",
                "message_id": "m3",
                "payload": {
                    "command_id": "c3",
                    "source": "cloud:web:tab",
                    "hold_id": "press-token",
                },
            },
        )
        released = runtime.input_dispatcher.dispatch.call_args_list[-1].args[0]
        assert released.action.value == "strobe_release"
        assert agent._leases.current_token("cloud:web:tab", "strobe") is None

    asyncio.run(_run())


def test_keepalive_does_not_extend_another_source() -> None:
    now = {"t": 0.0}
    leases = CloudHoldLeases(clock=lambda: now["t"])
    leases.arm("cloud:web:one", "strobe", "h1")
    leases.arm("cloud:web:two", "sweep", "h2")
    now["t"] = 7.0
    assert leases.refresh("h1", "cloud:web:two") is False
    assert leases.refresh("h2", "cloud:web:two") is True
    now["t"] = 8.0
    expired = leases.expired()
    assert [item.token for item in expired] == ["h1"]
    assert leases.current_token("cloud:web:two", "sweep") == "h2"


def test_expired_hold_releases_without_a_heartbeat() -> None:
    from orng_led.cloud.budget import HEARTBEAT_INTERVAL_S
    from orng_led.cloud.hold_lease import CLOUD_HOLD_LEASE_S

    assert HEARTBEAT_INTERVAL_S > CLOUD_HOLD_LEASE_S
    runtime = MagicMock()
    runtime.input_dispatcher.dispatch = MagicMock()
    agent = CloudDeviceAgent(runtime)
    now = {"t": 0.0}
    agent._leases = CloudHoldLeases(clock=lambda: now["t"])
    agent._leases.arm("cloud:web:tab", "strobe", "h1")
    now["t"] = CLOUD_HOLD_LEASE_S + 0.1
    agent._expire_cloud_holds()
    released = runtime.input_dispatcher.dispatch.call_args.args[0]
    assert released.action.value == "strobe_release"
    assert released.hold_token == "h1"
    assert agent._leases.current_token("cloud:web:tab", "strobe") is None


def test_a_dropped_socket_does_not_wait_out_a_refused_upgrade() -> None:
    from orng_led.cloud.agent import _upgrade_refused

    assert _upgrade_refused("server rejected WebSocket connection: HTTP 500") is True
    assert _upgrade_refused("no close frame received or sent") is False


def test_replacing_a_lease_does_not_expire_the_new_hold() -> None:
    now = {"t": 0.0}
    leases = CloudHoldLeases(clock=lambda: now["t"])
    leases.arm("cloud:web:tab", "strobe", "old")
    now["t"] = 5.0
    leases.arm("cloud:web:tab", "strobe", "new")
    now["t"] = 9.0
    assert leases.expired() == []
    assert leases.owns("cloud:web:tab", "strobe", "old") is False
    assert leases.owns("cloud:web:tab", "strobe", "new") is True
