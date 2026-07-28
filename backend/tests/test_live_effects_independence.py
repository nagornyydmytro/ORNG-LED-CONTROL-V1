"""Live Effects must light target fixtures independently of the preset frame."""

from __future__ import annotations

from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.config import default_config_dir, global_channel, load_show_config
from orng_led.config.models import ChannelDefinition, ChannelRole, FixtureKind
from orng_led.engine.engine import Engine
from orng_led.engine.intents import BarIntent, BeamIntent, ParIntent, Rgbw, StageIntent
from orng_led.engine.layers import WHITE, apply_strobe, apply_white_hit
from orng_led.engine.presets import NONE_PRESET_ID
from orng_led.engine.renderer import (
    analyze_live_effect_coverage,
    missing_roles_for_intent,
    render_stage,
)
from orng_led.main import create_app
from orng_led.output import RecordingSocket, TransportKind
from orng_led.presets.store import PresetStore

VENUE_IP = "2.0.0.11"
SAFE_IP = "127.0.0.1"


def _engine(preset_id: str = "P05") -> Engine:
    show = load_show_config()
    programs = PresetStore.load(default_config_dir() / "presets").programs()
    programs[NONE_PRESET_ID] = __import__(
        "orng_led.engine.presets", fromlist=["NonePresetProgram"]
    ).NonePresetProgram()
    engine = Engine(show=show, presets=programs, active_preset_id=preset_id)
    engine.set_blackout(False)
    engine.set_face(False)
    return engine


def _fixture_sum(frame: list[int], show, fixture_id: str) -> int:
    fx = next(f for f in show.patch.fixtures if f.id == fixture_id)
    profile = show.profiles[fx.profile_id]
    total = 0
    for local in range(1, profile.footprint + 1):
        total += frame[global_channel(fx.start_address, local) - 1]
    return total


def test_white_hit_on_zero_base_targets_all_rear() -> None:
    engine = _engine(NONE_PRESET_ID)
    engine.trigger_white_hit()
    snap = engine.tick(dt_s=0.05)
    from orng_led.engine.layers import rear_fixture_ids

    targets = rear_fixture_ids(engine.show)
    assert len(targets) >= 8
    # At least PARs and bars must light via mapped dimmer/color.
    assert _fixture_sum(snap.frame, engine.show, "par_1") > 0
    assert _fixture_sum(snap.frame, engine.show, "bar_1") > 0


def test_white_hit_ignores_preset_nonzero_mask() -> None:
    """Only one PAR lit in a hand-built base — White Hit still forces all rear intents."""
    show = load_show_config()
    base = StageIntent(fixtures={"par_1": ParIntent(color=Rgbw(r=1, g=0, b=0), intensity=0.4)})
    stage = apply_white_hit(StageIntent(fixtures=dict(base.fixtures)), show)
    rear = [fx.id for fx in show.patch.fixtures if fx.kind is not FixtureKind.FACE_PAR]
    for fixture_id in rear:
        assert fixture_id in stage.fixtures
        intent = stage.fixtures[fixture_id]
        if isinstance(intent, ParIntent):
            assert intent.intensity == 1.0
            assert intent.color == WHITE
        elif isinstance(intent, BarIntent):
            assert intent.dimmer == 1.0
        elif isinstance(intent, BeamIntent):
            assert intent.dimmer == 1.0
            assert intent.shutter_open is True


def test_white_hit_sets_dimmer_and_rgb_roles() -> None:
    engine = _engine(NONE_PRESET_ID)
    engine.trigger_white_hit()
    frame = engine.tick(dt_s=0.02).frame
    # par_1: CH1 dimmer, CH2 red mapped
    assert frame[0] == 255
    assert frame[1] == 255


def test_strobe_lights_targets_on_zero_base() -> None:
    stage = apply_strobe(StageIntent(), load_show_config(), now=0.0)
    assert "par_2" in stage.fixtures
    assert isinstance(stage.fixtures["par_2"], ParIntent)
    assert stage.fixtures["par_2"].intensity == 1.0


def test_face_only_targets_face_group() -> None:
    engine = _engine(NONE_PRESET_ID)
    engine.set_face(True, brightness=1.0)
    snap = engine.tick(dt_s=0.05)
    assert _fixture_sum(snap.frame, engine.show, "face_par_1") > 0
    assert _fixture_sum(snap.frame, engine.show, "par_1") == 0
    assert _fixture_sum(snap.frame, engine.show, "bar_1") == 0


def test_white_hit_release_restores_preset_or_zero() -> None:
    engine = _engine("P05")
    engine.tick(dt_s=0.5)
    before = list(engine.tick(dt_s=0.0).frame)
    engine.trigger_white_hit()
    lit = engine.tick(dt_s=0.02).frame
    assert sum(lit) > sum(before)
    engine.overlays.white_hit_until = None
    after = engine.tick(dt_s=0.02).frame
    # Preset look returns (not stuck on white-hit levels for unmapped zeros).
    assert sum(after) > 0


def test_white_hit_under_blackout_then_zero() -> None:
    engine = _engine("P05")
    engine.set_blackout(True)
    engine.trigger_white_hit()
    lit = engine.tick(dt_s=0.02).frame
    assert any(lit)
    engine.overlays.white_hit_until = None
    dark = engine.tick(dt_s=0.02).frame
    assert dark == [0] * 512


def test_episode_change_during_white_hit_keeps_effect() -> None:
    engine = _engine("P05")
    engine.trigger_white_hit()
    engine.tick(dt_s=0.02)
    engine.seek_episode(3)
    snap = engine.tick(dt_s=0.02)
    assert snap.white_hit_active is True
    assert _fixture_sum(snap.frame, engine.show, "par_1") > 0


def test_incomplete_beam_mapping_reported() -> None:
    show = load_show_config()
    intent = BeamIntent(dimmer=1.0, color=WHITE, shutter_open=True)
    beam = next(fx for fx in show.patch.fixtures if fx.kind is FixtureKind.BEAM)
    profile = show.profile_for(beam)
    missing = missing_roles_for_intent(profile, intent)
    assert "Color Wheel: White/Open" in missing
    coverage = analyze_live_effect_coverage(
        show,
        StageIntent(fixtures={beam.id: intent}),
        "white_hit",
        [beam.id],
    )
    assert coverage["skipped"]


def test_color_wheel_white_open_uses_saved_value(tmp_path) -> None:
    show = load_show_config()
    beam = next(fx for fx in show.patch.fixtures if fx.kind is FixtureKind.BEAM)
    profile = show.profiles["beam_13ch"]
    channels = list(profile.channels)
    channels[0] = ChannelDefinition(local=1, role=ChannelRole.DIMMER)
    channels[1] = ChannelDefinition(
        local=2,
        role=ChannelRole.COLOR,
        control_values={"open": 42, "white": 42},
        palette={"off": 0, "white": 42},
    )
    channels[2] = ChannelDefinition(
        local=3,
        role=ChannelRole.SHUTTER,
        control_values={"open": 200, "closed": 0},
    )
    show.profiles["beam_13ch"] = profile.model_copy(update={"channels": channels})
    stage = StageIntent(fixtures={beam.id: BeamIntent(dimmer=1.0, color=WHITE, shutter_open=True)})
    frame = render_stage(show, stage, {})
    assert frame[global_channel(beam.start_address, 1) - 1] == 255
    assert frame[global_channel(beam.start_address, 2) - 1] == 42
    assert frame[global_channel(beam.start_address, 3) - 1] == 200


def test_white_hit_uses_rgb_when_mapped(tmp_path) -> None:
    import shutil

    source = default_config_dir()
    cfg = tmp_path / "config"
    shutil.copytree(source, cfg)
    runtime = AppRuntime.create(autostart_loop=False, config_dir=cfg)
    profile = runtime.show.profiles["par_7ch_provisional"].model_dump(mode="json")
    for ch in profile["channels"]:
        if ch["local"] == 2:
            ch["role"] = "red"
        if ch["local"] == 3:
            ch["role"] = "green"
            ch["label"] = "Green"
        if ch["local"] == 4:
            ch["role"] = "blue"
            ch["label"] = "Blue"
    runtime.save_profile(profile)
    runtime.engine.set_blackout(False)
    runtime.apply_select_preset(NONE_PRESET_ID, reset_clock=True)
    runtime.engine.trigger_white_hit()
    frame = runtime.tick(dt_s=0.02).frame
    assert frame[0] == 255
    assert frame[1] == 255
    assert frame[2] == 255
    assert frame[3] == 255


def test_strobe_and_color_hit_ignore_preset_mask() -> None:
    show = load_show_config()
    base = StageIntent(fixtures={"par_1": ParIntent(color=Rgbw(r=1, g=0, b=0), intensity=0.2)})
    from orng_led.engine.layers import apply_color_hit

    color = Rgbw(r=0.0, g=1.0, b=0.0)
    stage = apply_color_hit(StageIntent(fixtures=dict(base.fixtures)), show, color)
    assert "bar_1" in stage.fixtures
    beam_id = next(fx.id for fx in show.patch.fixtures if fx.kind is FixtureKind.BEAM)
    assert beam_id in stage.fixtures
    strobe = apply_strobe(StageIntent(fixtures=dict(base.fixtures)), show, now=0.0)
    assert "bar_2" in strobe.fixtures


def test_viz_and_renderer_share_live_effect_targets() -> None:
    from orng_led.engine.layers import rear_fixture_ids, target_fixture_ids_for_effect

    show = load_show_config()
    targets = target_fixture_ids_for_effect("white_hit", show)
    assert targets == rear_fixture_ids(show)
    stage = apply_white_hit(StageIntent(), show)
    assert set(stage.fixtures) == set(targets)
    # Simulator only lights fixtures that actually received DMX (mapped roles).
    frame = render_stage(show, stage, {})
    from orng_led.simulator.decode import decode_simulator_view

    sim = decode_simulator_view(show, frame)
    lit_pars = {p.id for p in sim.pars if p.intensity > 0.02}
    assert "par_1" in lit_pars
    # Unmapped beams stay dark in both wire and sim.
    dark_beams = {b.id for b in sim.beams if b.dimmer <= 0.02}
    beam_id = next(fx.id for fx in show.patch.fixtures if fx.kind is FixtureKind.BEAM)
    assert beam_id in dark_beams

    show = load_show_config()
    bar = next(fx for fx in show.patch.fixtures if fx.id == "bar_1")
    stage = StageIntent(
        fixtures={
            bar.id: BarIntent(segments=(1.0,) * 8, dimmer=1.0, color=WHITE),
        }
    )
    frame = render_stage(show, stage, {})
    # whole_color local 12 → global start+11
    whole = frame[global_channel(bar.start_address, 12) - 1]
    assert whole == 64  # default palette white


def test_program_off_written_when_mapped(tmp_path) -> None:
    show = load_show_config()
    # Inject a temporary program role on PAR profile in memory.
    profile = show.profiles["par_7ch_provisional"]
    channels = list(profile.channels)
    channels[2] = ChannelDefinition(local=3, role=ChannelRole.PROGRAM, control_values={"off": 10})
    show.profiles["par_7ch_provisional"] = profile.model_copy(update={"channels": channels})
    stage = StageIntent(
        fixtures={"par_1": ParIntent(color=WHITE, intensity=1.0)},
    )
    frame = render_stage(show, stage, {})
    assert frame[2] == 10  # local 3 program off


def test_disarm_blocks_white_hit_on_artnet() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    sock = RecordingSocket()
    runtime.output.configure_artnet(target_ip=SAFE_IP, injected_socket=sock)
    runtime.activate_artnet_network(confirmed=True, allow_real_udp=True)
    assert runtime.output.armed is False
    runtime.engine.set_blackout(False)
    runtime.engine.trigger_white_hit()
    runtime.tick(dt_s=0.05)
    assert runtime.build_state().output.wire_nonzero_channels == 0
    assert all(addr[0] != VENUE_IP for _, addr in sock.sent)
    assert runtime.output.transport_kind is TransportKind.ARTNET


def test_live_effects_state_exposes_targets_and_warnings() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.engine.set_blackout(False)
    runtime.apply_select_preset(NONE_PRESET_ID, reset_clock=True)
    runtime.engine.trigger_white_hit()
    state = runtime.build_state()
    assert state.live_effects is not None
    assert state.live_effects["active"] is True
    assert "white_hit" in state.live_effects["active_ids"]
    effects = state.live_effects["effects"]
    assert effects
    assert "par_1" in effects[0]["target_fixture_ids"]


def test_mapping_change_clears_old_channels_and_reroutes(tmp_path) -> None:
    import shutil

    source = default_config_dir()
    cfg = tmp_path / "config"
    shutil.copytree(source, cfg)
    runtime = AppRuntime.create(autostart_loop=False, config_dir=cfg)
    runtime.engine.set_blackout(False)
    runtime.apply_select_preset(NONE_PRESET_ID, reset_clock=True)
    runtime.engine.trigger_white_hit()
    runtime.tick(dt_s=0.02)
    profile = runtime.show.profiles["par_7ch_provisional"].model_dump(mode="json")
    # Move red from local 2 to local 4
    for ch in profile["channels"]:
        if ch["local"] == 2:
            ch["role"] = "unused"
        if ch["local"] == 4:
            ch["role"] = "red"
            ch["label"] = "Red"
    runtime.save_profile(profile)
    runtime.engine.trigger_white_hit()
    frame = runtime.tick(dt_s=0.02).frame
    assert frame[1] == 0  # old red local cleared
    assert frame[3] == 255  # new red local


def test_api_never_targets_venue_ip_during_live_fx() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app) as client:
        client.post("/api/commands/blackout", json={"enabled": False})
        client.post("/api/commands/white-hit", json={})
        client.post("/api/commands/strobe", json={"action": "press"})
        state = client.get("/api/state").json()
        assert state["output"]["target_ip"] != VENUE_IP or state["output"]["armed"] is False
        client.post("/api/commands/strobe", json={"action": "release"})
