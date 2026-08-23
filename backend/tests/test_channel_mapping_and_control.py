"""Channel mapping, none-preset, episode seek, live FX under blackout."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.config.models import ChannelRole
from orng_led.config.validation import global_channel
from orng_led.engine.presets import NONE_PRESET_ID
from orng_led.engine.show_whitelist import assert_lights_dark
from orng_led.main import create_app
from orng_led.output import RecordingSocket, TransportKind

VENUE_IP = "2.0.0.11"
SAFE_IP = "127.0.0.1"


def _runtime(tmp_path: Path | None = None) -> AppRuntime:
    if tmp_path is None:
        return AppRuntime.create(autostart_loop=False)
    # Copy is heavy; default config_dir is fine for most tests.
    return AppRuntime.create(autostart_loop=False)


def test_profiles_have_7_15_13_footprints() -> None:
    runtime = _runtime()
    par = runtime.show.profiles["par_7ch_provisional"]
    bar = runtime.show.profiles["bar_15ch"]
    beam = runtime.show.profiles["beam_13ch"]
    assert par.footprint == 7 and len(par.channels) == 7
    assert bar.footprint == 15 and len(bar.channels) == 15
    assert beam.footprint == 13 and len(beam.channels) == 13
    assert all(fx.profile_id != "bar_16ch_provisional" for fx in runtime.show.patch.fixtures)
    assert all(fx.profile_id != "beam_32ch_provisional" for fx in runtime.show.patch.fixtures)


def test_mapping_save_and_reload_changes_routing(tmp_path: Path) -> None:
    runtime = _runtime()
    profile = runtime.show.profiles["par_7ch_provisional"]
    original = profile.model_dump(mode="json")
    data = profile.model_dump(mode="json")
    for ch in data["channels"]:
        if ch["local"] == 3:
            ch["role"] = "green"
            ch["label"] = "Green"
    try:
        runtime.save_profile(data)
        reloaded = AppRuntime.create(autostart_loop=False)
        assert reloaded.show.profiles["par_7ch_provisional"].channels[2].role is ChannelRole.GREEN
    finally:
        # Restore committed baseline mapping so other tests stay isolated.
        runtime.save_profile(original)


def test_unassigned_channel_stays_zero() -> None:
    runtime = _runtime()
    runtime.engine.set_blackout(False)
    runtime.apply_select_preset("P10", reset_clock=True)
    runtime.tick(dt_s=0.1)
    beam = next(fx for fx in runtime.show.patch.fixtures if fx.id == "beam_right")
    profile = runtime.show.profile_for(beam)
    frame = runtime.build_state().frame
    for channel in profile.channels:
        if channel.role is not ChannelRole.UNUSED:
            continue
        index = global_channel(beam.start_address, channel.local) - 1
        assert frame[index] == 0


def test_fixture_slider_only_touches_selected_fixture_globals() -> None:
    runtime = _runtime()
    runtime.begin_fixture_channel_test("par_2")
    runtime.set_fixture_local_channel("par_2", 1, 64)
    runtime.set_fixture_local_channel("par_2", 2, 255)
    state = runtime.build_state()
    assert state.output.source_frame_sum == 64 + 255
    assert state.raw_tester["frame"][7] == 64  # par_2 starts at 8
    assert state.raw_tester["frame"][8] == 255
    assert state.raw_tester["frame"][0] == 0  # par_1 untouched
    assert_lights_dark(runtime.show, state.frame)  # Blackout holds wire lights dark


def test_fixture_test_reset_and_end_zeros() -> None:
    runtime = _runtime()
    runtime.begin_fixture_channel_test("par_1")
    runtime.set_fixture_local_channel("par_1", 1, 100)
    runtime.reset_fixture_channel_test("par_1")
    assert runtime.raw_tester.nonzero_channels == 0
    runtime.set_fixture_local_channel("par_1", 2, 50)
    sock_before = runtime.output.frames_sent
    runtime.end_fixture_channel_test()
    assert runtime.raw_tester.active is False
    assert runtime.engine.overlays.blackout is True
    assert_lights_dark(runtime.show, runtime.build_state().frame)
    assert runtime.output.frames_sent >= sock_before


def test_blackout_zeros_preset_but_live_fx_lights() -> None:
    runtime = _runtime()
    runtime.engine.set_blackout(True)
    runtime.apply_select_preset("P10", reset_clock=True)
    runtime.tick(dt_s=0.05)
    assert_lights_dark(runtime.show, runtime.build_state().frame)

    runtime.apply_white_hit()
    runtime.tick(dt_s=0.02)
    assert any(runtime.build_state().frame)

    runtime.engine.overlays.white_hit_until = None
    runtime.tick(dt_s=0.02)
    assert_lights_dark(runtime.show, runtime.build_state().frame)


def test_none_preset_zero_base_without_blackout() -> None:
    runtime = _runtime()
    runtime.engine.set_blackout(False)
    runtime.apply_select_preset(NONE_PRESET_ID, reset_clock=True)
    runtime.tick(dt_s=0.1)
    state = runtime.build_state()
    assert state.engine.preset_id == NONE_PRESET_ID
    assert state.engine.blackout is False
    assert state.engine.episode_count == 0
    assert_lights_dark(runtime.show, state.frame)

    runtime.apply_white_hit()
    runtime.tick(dt_s=0.02)
    assert any(runtime.build_state().frame)


def test_seek_episode_updates_runtime_immediately() -> None:
    runtime = _runtime()
    runtime.engine.set_blackout(True)
    runtime.apply_select_preset("P05", reset_clock=True)
    assert runtime.engine.preset_elapsed_s == 0.0
    runtime.apply_seek_episode(3)
    assert runtime.engine.preset_elapsed_s == pytest.approx(3 * 18.0)
    state = runtime.build_state()
    assert state.engine.episode_index == 3
    assert state.engine.episode_time_s == pytest.approx(0.0)
    # Blackout keeps preset look dark (Beam axes / FIXED may remain).
    assert_lights_dark(runtime.show, state.frame)


def test_seek_then_auto_continues_to_next_episode() -> None:
    runtime = _runtime()
    runtime.apply_select_preset("P05", reset_clock=True)
    runtime.apply_seek_episode(1)
    runtime.engine.set_blackout(False)
    # Jump near the end of episode 1, then tick past the boundary.
    doc = runtime.preset_store.documents["P05"]
    start = sum(float(ep.duration_s) for ep in doc.episodes[:1])
    runtime.engine.preset_elapsed_s = start + float(doc.episodes[1].duration_s) - 0.05
    runtime.tick(dt_s=0.1)
    assert runtime.build_state().engine.episode_index == 2


def test_auto_and_manual_episode_nudge_wrap_around() -> None:
    """Last → next returns to first for both the clock and Zoom/UI nudge."""
    runtime = _runtime()
    runtime.apply_select_preset("P05", reset_clock=True)
    runtime.engine.set_blackout(False)
    doc = runtime.preset_store.documents["P05"]
    count = len(doc.episodes)
    assert count >= 2

    # Automatic: cross the end of the last episode → episode 0.
    total = sum(float(ep.duration_s) for ep in doc.episodes)
    runtime.engine.preset_elapsed_s = total - 0.05
    runtime.engine._resync_phase_turns()
    runtime.tick(dt_s=0.1)
    assert runtime.build_state().engine.episode_index == 0

    # Manual nudge: last → +1 wraps to first; first → -1 wraps to last.
    runtime.apply_seek_episode(count - 1)
    runtime.apply_nudge_episode(1)
    assert runtime.build_state().engine.episode_index == 0
    runtime.apply_nudge_episode(-1)
    assert runtime.build_state().engine.episode_index == count - 1


def test_none_preset_hides_episode_activity() -> None:
    runtime = _runtime()
    runtime.apply_select_preset(NONE_PRESET_ID)
    state = runtime.build_state()
    assert state.engine.episode_count == 0
    assert state.engine.episode_index == 0


def test_disarm_blocks_live_fx_on_artnet() -> None:
    runtime = _runtime()
    sock = RecordingSocket()
    runtime.output.configure_artnet(target_ip=SAFE_IP, injected_socket=sock)
    runtime.activate_artnet_network(confirmed=True, allow_real_udp=True)
    runtime.arm_output(confirmed=True)
    runtime.engine.set_blackout(False)
    runtime.apply_white_hit()
    runtime.tick(dt_s=0.05)
    assert any(sock.sent[-1][0][-512:])

    runtime.disarm_output()
    runtime.apply_white_hit()
    before = len(sock.sent)
    runtime.tick(dt_s=0.05)
    assert runtime.output.armed is False
    assert_lights_dark(runtime.show, list(sock.sent[-1][0][-512:]))
    assert all(addr[0] != VENUE_IP for _, addr in sock.sent)
    assert runtime.output.transport_kind is TransportKind.ARTNET
    assert len(sock.sent) >= before


def test_api_channel_roles_and_seek() -> None:
    runtime = _runtime()
    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app) as client:
        roles = client.get("/api/setup/channel-roles").json()["roles"]
        assert any(item["role"] == "dimmer" for item in roles)
        assert any("Фіксоване" in item["label"] for item in roles)

        client.post("/api/commands/select-preset", json={"preset_id": "P05"})
        seek = client.post("/api/commands/seek-episode", json={"episode_index": 2})
        assert seek.status_code == 200
        assert seek.json()["state"]["engine"]["episode_index"] == 2

        none = client.post("/api/commands/select-preset", json={"preset_id": "NONE"})
        assert none.status_code == 200
        assert none.json()["state"]["engine"]["preset_id"] == "NONE"
