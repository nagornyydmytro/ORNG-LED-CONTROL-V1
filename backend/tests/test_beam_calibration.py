"""Beam ceiling orientation + per-head calibration transforms."""

from __future__ import annotations

import math
import shutil

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.config import default_config_dir, global_channel, load_show_config
from orng_led.config.models import (
    ChannelDefinition,
    ChannelRole,
    ConfigError,
    SpatialPlacement,
)
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.beam_transform import (
    decode_axis_dmx,
    encode_axis_dmx,
    physical_to_semantic,
    semantic_to_physical,
    transform_axis,
)
from orng_led.engine.engine import Engine
from orng_led.engine.intents import BeamIntent, Rgbw, StageIntent
from orng_led.engine.renderer import render_stage
from orng_led.main import create_app
from orng_led.output import RecordingSocket
from orng_led.simulator.decode import decode_simulator_view

VENUE_IP = "2.0.0.11"
SAFE_IP = "127.0.0.1"


def test_layout_beams_are_ceiling_mounted() -> None:
    show = load_show_config()
    left = show.layout.placement_for("beam_left")
    right = show.layout.placement_for("beam_right")
    assert left is not None and right is not None
    assert left.mount.value == "ceiling"
    assert right.mount.value == "ceiling"
    assert left.x < right.x
    assert show.patch.fixture("beam_left").spatial.side.value == "left"
    assert show.patch.fixture("beam_right").spatial.side.value == "right"


def test_legacy_patch_loads_beam_calibration_defaults() -> None:
    show = load_show_config()
    for fixture_id in ("beam_left", "beam_right"):
        spatial = show.patch.fixture(fixture_id).spatial
        assert spatial.pan_invert is False
        assert spatial.tilt_invert is False
        assert spatial.pan_offset == 0.0
        assert spatial.tilt_offset == 0.0
        assert spatial.pan_min == 0.0
        assert spatial.pan_max == 1.0
        assert spatial.home_pan == 0.5
        assert spatial.beam_calibration_confirmed is False


def test_transform_identity_preserves_midpoint() -> None:
    spatial = SpatialPlacement(side="left")
    pan, tilt = semantic_to_physical(spatial, pan=0.5, tilt=0.5)
    assert math.isclose(pan, 0.5)
    assert math.isclose(tilt, 0.5)


def test_pan_invert_and_tilt_invert() -> None:
    spatial = SpatialPlacement(side="left", pan_invert=True, tilt_invert=True)
    pan, tilt = semantic_to_physical(spatial, pan=0.25, tilt=0.1)
    assert math.isclose(pan, 0.75)
    assert math.isclose(tilt, 0.9)


def test_offset_applied_in_semantic_space() -> None:
    spatial = SpatialPlacement(side="left", pan_offset=0.1)
    pan, _ = semantic_to_physical(spatial, pan=0.4, tilt=0.5)
    assert math.isclose(pan, 0.5)


def test_min_max_clamp_physical_output() -> None:
    spatial = SpatialPlacement(side="left", pan_min=0.2, pan_max=0.6)
    pan, _ = semantic_to_physical(spatial, pan=1.0, tilt=0.5)
    assert math.isclose(pan, 0.6)
    pan0, _ = semantic_to_physical(spatial, pan=0.0, tilt=0.5)
    assert math.isclose(pan0, 0.2)


def test_invalid_min_max_rejected() -> None:
    with pytest.raises((ConfigError, Exception), match="pan_min"):
        SpatialPlacement(side="left", pan_min=0.8, pan_max=0.2)


def test_8bit_and_16bit_encode_decode() -> None:
    eight = encode_axis_dmx(0.5, has_fine=False)
    assert eight.fine is None
    assert eight.coarse == 128
    assert math.isclose(decode_axis_dmx(eight.coarse, None), 128 / 255.0, rel_tol=1e-3)

    sixteen = encode_axis_dmx(0.5, has_fine=True)
    assert sixteen.fine is not None
    assert sixteen.value_16bit == 32768 or sixteen.value_16bit == 32767
    restored = decode_axis_dmx(sixteen.coarse, sixteen.fine)
    assert math.isclose(restored, 0.5, abs_tol=2 / 65535)


def test_forward_inverse_roundtrip() -> None:
    spatial = SpatialPlacement(
        side="right",
        pan_invert=True,
        tilt_offset=-0.05,
        pan_min=0.1,
        pan_max=0.9,
        tilt_min=0.05,
        tilt_max=0.95,
    )
    for semantic in (0.0, 0.25, 0.5, 0.75, 1.0):
        physical = transform_axis(
            semantic,
            offset=spatial.pan_offset,
            invert=spatial.pan_invert,
            axis_min=spatial.pan_min,
            axis_max=spatial.pan_max,
        )
        back = physical_to_semantic(spatial, pan=physical, tilt=0.5)[0]
        assert abs(back - semantic) < 1e-6


def test_simulator_does_not_double_invert() -> None:
    show = load_show_config()
    beam = show.patch.fixture("beam_left")
    profile = show.profiles[beam.profile_id]
    channels = list(profile.channels)
    channels[0] = ChannelDefinition(local=1, role=ChannelRole.PAN_COARSE)
    channels[1] = ChannelDefinition(local=2, role=ChannelRole.TILT_COARSE)
    show.profiles[beam.profile_id] = profile.model_copy(update={"channels": channels})
    show.patch.fixtures = [
        fx.model_copy(update={"spatial": fx.spatial.model_copy(update={"pan_invert": True})})
        if fx.id == beam.id
        else fx
        for fx in show.patch.fixtures
    ]
    beam = show.patch.fixture("beam_left")
    stage = StageIntent(
        fixtures={beam.id: BeamIntent(pan=0.25, tilt=0.5, dimmer=0.0, shutter_open=False)}
    )
    # Confirm calibration so light gating is irrelevant; motion still written.
    show.patch.fixtures = [
        fx.model_copy(
            update={
                "spatial": fx.spatial.model_copy(
                    update={"pan_invert": True, "beam_calibration_confirmed": True}
                )
            }
        )
        if fx.id == beam.id
        else fx
        for fx in show.patch.fixtures
    ]
    beam = show.patch.fixture("beam_left")
    frame = render_stage(show, stage, {beam.id: BeamMotionState(pan=0.25, tilt=0.5)})
    view = decode_simulator_view(show, frame)
    left = next(b for b in view.beams if b.id == "beam_left")
    assert math.isclose(left.pan, 0.25, abs_tol=0.02)


def test_independent_left_right_calibration() -> None:
    show = load_show_config()
    left = show.patch.fixture("beam_left")
    right = show.patch.fixture("beam_right")
    left_spatial = left.spatial.model_copy(update={"pan_invert": True, "pan_offset": 0.1})
    right_spatial = right.spatial.model_copy(
        update={"tilt_invert": True, "pan_min": 0.2, "pan_max": 0.8}
    )
    lp, _ = semantic_to_physical(left_spatial, pan=0.3, tilt=0.5)
    rp, _ = semantic_to_physical(right_spatial, pan=0.3, tilt=0.5)
    assert not math.isclose(lp, rp)


def test_home_initializes_motion_state() -> None:
    show = load_show_config()
    fixtures = [
        fx.model_copy(
            update={"spatial": fx.spatial.model_copy(update={"home_pan": 0.2, "home_tilt": 0.8})}
        )
        if fx.id == "beam_left"
        else fx
        for fx in show.patch.fixtures
    ]
    show = show.model_copy(update={"patch": show.patch.model_copy(update={"fixtures": fixtures})})
    engine = Engine(show=show)
    assert math.isclose(engine.beam_motion["beam_left"].pan, 0.2)
    assert math.isclose(engine.beam_motion["beam_left"].tilt, 0.8)


def test_per_fixture_speed_limits() -> None:
    show = load_show_config()
    fixtures = [
        fx.model_copy(
            update={
                "spatial": fx.spatial.model_copy(
                    update={"max_pan_speed": 0.1, "max_tilt_speed": 0.05}
                )
            }
        )
        if fx.id == "beam_left"
        else fx
        for fx in show.patch.fixtures
    ]
    show = show.model_copy(update={"patch": show.patch.model_copy(update={"fixtures": fixtures})})
    engine = Engine(show=show)
    limits = engine.beam_limits_for("beam_left")
    assert math.isclose(limits.max_pan_speed, 0.1)
    assert math.isclose(limits.max_tilt_speed, 0.05)


def test_unconfirmed_beam_blocks_physical_light() -> None:
    show = load_show_config()
    beam = show.patch.fixture("beam_left")
    profile = show.profiles[beam.profile_id]
    channels = list(profile.channels)
    channels[0] = ChannelDefinition(local=1, role=ChannelRole.DIMMER)
    channels[1] = ChannelDefinition(local=2, role=ChannelRole.PAN_COARSE)
    channels[2] = ChannelDefinition(local=3, role=ChannelRole.TILT_COARSE)
    show.profiles[beam.profile_id] = profile.model_copy(update={"channels": channels})
    assert beam.spatial.beam_calibration_confirmed is False
    stage = StageIntent(
        fixtures={
            beam.id: BeamIntent(
                pan=0.5, tilt=0.5, dimmer=1.0, color=Rgbw(r=1, g=1, b=1), shutter_open=True
            )
        }
    )
    frame = render_stage(show, stage, {beam.id: BeamMotionState()})
    dimmer = frame[global_channel(beam.start_address, 1) - 1]
    assert dimmer == 0
    view = decode_simulator_view(show, frame)
    left = next(b for b in view.beams if b.id == beam.id)
    assert left.calibration_confirmed is False
    assert left.calibration_blocker


def test_calibration_test_requires_pan_role() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    with pytest.raises(ValueError, match="Pan Coarse"):
        runtime.begin_beam_calibration_test("beam_left", confirmed=True)


def test_calibration_test_begin_does_not_move_or_light(tmp_path) -> None:
    source = default_config_dir()
    cfg = tmp_path / "config"
    shutil.copytree(source, cfg)
    runtime = AppRuntime.create(autostart_loop=False, config_dir=cfg)
    profile = runtime.show.profiles["beam_13ch"].model_dump(mode="json")
    for ch in profile["channels"]:
        if ch["local"] == 1:
            ch["role"] = "pan_coarse"
        if ch["local"] == 2:
            ch["role"] = "tilt_coarse"
        if ch["local"] == 3:
            ch["role"] = "dimmer"
    runtime.save_profile(profile)
    state = runtime.begin_beam_calibration_test("beam_left", confirmed=True)
    assert state.beam_calibration["session"]["active"] is True
    assert sum(state.frame) == 0
    assert state.beam_calibration["session"]["visible_beam_on"] is False


def test_only_one_beam_calibration_at_a_time(tmp_path) -> None:
    source = default_config_dir()
    cfg = tmp_path / "config"
    shutil.copytree(source, cfg)
    runtime = AppRuntime.create(autostart_loop=False, config_dir=cfg)
    profile = runtime.show.profiles["beam_13ch"].model_dump(mode="json")
    for ch in profile["channels"]:
        if ch["local"] == 1:
            ch["role"] = "pan_coarse"
        if ch["local"] == 2:
            ch["role"] = "tilt_coarse"
    runtime.save_profile(profile)
    runtime.begin_beam_calibration_test("beam_left", confirmed=True)
    with pytest.raises(ValueError, match="іншої голови"):
        runtime.begin_beam_calibration_test("beam_right", confirmed=True)


def test_visible_beam_blocked_by_blackout(tmp_path) -> None:
    source = default_config_dir()
    cfg = tmp_path / "config"
    shutil.copytree(source, cfg)
    runtime = AppRuntime.create(autostart_loop=False, config_dir=cfg)
    profile = runtime.show.profiles["beam_13ch"].model_dump(mode="json")
    for ch in profile["channels"]:
        if ch["local"] == 1:
            ch["role"] = "pan_coarse"
        if ch["local"] == 2:
            ch["role"] = "tilt_coarse"
        if ch["local"] == 3:
            ch["role"] = "dimmer"
        if ch["local"] == 4:
            ch["role"] = "shutter"
            ch["control_values"] = {"open": 255, "closed": 0}
        if ch["local"] == 5:
            ch["role"] = "color"
            ch["palette"] = {"off": 0, "white": 64}
    runtime.save_profile(profile)
    runtime.begin_beam_calibration_test("beam_left", confirmed=True)
    runtime.engine.set_blackout(True)
    with pytest.raises(ValueError, match="Blackout"):
        runtime.set_beam_calibration_visible(enabled=True, confirmed=True)


def test_end_clears_calibration_and_disarm_ends_session(tmp_path) -> None:
    source = default_config_dir()
    cfg = tmp_path / "config"
    shutil.copytree(source, cfg)
    runtime = AppRuntime.create(autostart_loop=False, config_dir=cfg)
    profile = runtime.show.profiles["beam_13ch"].model_dump(mode="json")
    for ch in profile["channels"]:
        if ch["local"] == 1:
            ch["role"] = "pan_coarse"
        if ch["local"] == 2:
            ch["role"] = "tilt_coarse"
    runtime.save_profile(profile)
    runtime.begin_beam_calibration_test("beam_left", confirmed=True)
    runtime.set_beam_calibration_position(pan=0.7, tilt=0.3)
    assert sum(runtime.beam_calibration.frame) > 0
    runtime.end_beam_calibration_test()
    assert runtime.beam_calibration.active is False
    assert runtime.build_state().output.source_owner != "beam_calibration_test"


def test_save_calibration_hot_reload(tmp_path) -> None:
    source = default_config_dir()
    cfg = tmp_path / "config"
    shutil.copytree(source, cfg)
    runtime = AppRuntime.create(autostart_loop=False, config_dir=cfg)
    runtime.save_beam_calibration(
        "beam_left",
        {"pan_invert": True, "home_pan": 0.3, "home_tilt": 0.7, "max_pan_speed": 0.2},
    )
    spatial = runtime.show.patch.fixture("beam_left").spatial
    assert spatial.pan_invert is True
    assert math.isclose(spatial.home_pan, 0.3)
    assert math.isclose(runtime.engine.beam_motion["beam_left"].pan, 0.3)
    reloaded = load_show_config(cfg)
    assert reloaded.patch.fixture("beam_left").spatial.pan_invert is True


def test_api_never_sends_to_venue_during_beam_cal(tmp_path) -> None:
    source = default_config_dir()
    cfg = tmp_path / "config"
    shutil.copytree(source, cfg)
    runtime = AppRuntime.create(autostart_loop=False, config_dir=cfg)
    sock = RecordingSocket()
    runtime.output.configure_artnet(target_ip=SAFE_IP, injected_socket=sock)
    profile = runtime.show.profiles["beam_13ch"].model_dump(mode="json")
    for ch in profile["channels"]:
        if ch["local"] == 1:
            ch["role"] = "pan_coarse"
        if ch["local"] == 2:
            ch["role"] = "tilt_coarse"
    runtime.save_profile(profile)
    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app) as client:
        client.post(
            "/api/setup/beam-calibration/begin",
            json={"fixture_id": "beam_left", "confirmed": True},
        )
        client.post("/api/setup/beam-calibration/set", json={"pan": 0.6, "tilt": 0.4})
        assert all(addr[0] != VENUE_IP for _, addr in sock.sent)
        client.post("/api/setup/beam-calibration/end", json={})
