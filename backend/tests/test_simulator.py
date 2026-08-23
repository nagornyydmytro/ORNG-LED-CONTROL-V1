"""Simulator decode and accelerated preview clock tests."""

from __future__ import annotations

from orng_led.api.runtime import AppRuntime
from orng_led.config import load_show_config
from orng_led.engine.engine import Engine
from orng_led.engine.show_whitelist import assert_lights_dark
from orng_led.simulator.decode import decode_simulator_view


def test_decode_counts_twelve_fixtures() -> None:
    show = load_show_config()
    engine = Engine(show=show)
    snap = engine.tick(dt_s=0.0)
    view = decode_simulator_view(show, list(snap.frame))
    assert len(view.pars) == 4
    assert len(view.bars) == 4
    assert len(view.beams) == 2
    assert len(view.faces) == 2
    assert all(len(bar.segments) == 8 for bar in view.bars)


def test_blackout_zeros_simulator_and_frame() -> None:
    show = load_show_config()
    engine = Engine(show=show)
    engine.select_preset("P05")
    engine.tick(dt_s=0.5)
    engine.set_blackout(True)
    snap = engine.tick(dt_s=0.0)
    frame = list(snap.frame)
    assert_lights_dark(show, frame)
    view = decode_simulator_view(show, frame)
    assert all(par.intensity == 0 for par in view.pars)
    assert all(bar.dimmer == 0 and sum(bar.segments) == 0 for bar in view.bars)
    assert all(beam.dimmer == 0 for beam in view.beams)
    assert all(face.intensity == 0 for face in view.faces)


def test_simulator_matches_frame_not_parallel_fiction() -> None:
    show = load_show_config()
    engine = Engine(show=show)
    engine.select_preset("P05")
    snap = engine.tick(dt_s=1.0)
    frame = list(snap.frame)
    view = decode_simulator_view(show, frame)

    # PAR 1 starts at address 1: dimmer local 1, red local 2 (hardware-confirmed).
    par1 = next(p for p in view.pars if p.id == "par_1")
    assert abs(par1.intensity - frame[0] / 255.0) < 1e-9
    # Preview RGB may be remapped to venue LED look; still tracks a lit red channel.
    if frame[1] > 0:
        assert par1.r > 0.05

    bar1 = next(b for b in view.bars if b.id == "bar_1")
    # Bar 1 start 93 → index 92 dimmer; segment_color locals 4..11 (no RGB locals).
    assert abs(bar1.dimmer - frame[92] / 255.0) < 1e-9
    # Encoded segment colour at local 4 → global 96 → index 95.
    if frame[95] > 0:
        assert bar1.segments[0] == 1.0
        # Palette DMX (e.g. amber=136) must decode to a real colour, not grey dimmer.
        assert bar1.r + bar1.g + bar1.b > 0.05
        assert abs(bar1.r - bar1.g) > 0.05 or abs(bar1.g - bar1.b) > 0.05
        assert len(bar1.segment_r) == 8
    else:
        assert bar1.segments[0] == 0.0
        if bar1.dimmer <= 0.02:
            assert bar1.r == 0.0
            assert bar1.g == 0.0
            assert bar1.b == 0.0


def test_preview_amber_reads_yellow_red_reads_orange() -> None:
    """Venue LED look: amber slot → yellow; red slot → orange-red — not pure sRGB."""
    from orng_led.config.models import ChannelDefinition, ChannelRole
    from orng_led.engine.renderer import _PALETTE_DISPLAY_RGB
    from orng_led.simulator.decode import _preview_rgb_from_channels, _rgb_from_palette_dmx

    amber_disp = _PALETTE_DISPLAY_RGB["amber"]
    red_disp = _PALETTE_DISPLAY_RGB["red"]
    assert amber_disp[1] > 0.8  # yellow-amber
    assert red_disp[1] > 0.2  # orange-red, not pure red

    channel = ChannelDefinition(
        local=4,
        role=ChannelRole.SEGMENT_COLOR,
        segment_index=1,
        palette={"off": 0, "red": 46, "amber": 136, "orange": 61, "blue": 106},
    )
    amber_rgb = _rgb_from_palette_dmx(channel, 136)
    red_rgb = _rgb_from_palette_dmx(channel, 46)
    assert amber_rgb is not None and amber_rgb[1] > amber_rgb[2] and amber_rgb[1] > 0.8
    assert red_rgb is not None and red_rgb[1] > 0.15

    # PAR mix for preset amber / deep_red remaps to the same venue look.
    par_amber = _preview_rgb_from_channels(1.0, 0.55, 0.08)
    par_red = _preview_rgb_from_channels(1.0, 0.05, 0.02)
    assert par_amber[1] > 0.75
    assert par_red[1] > 0.15


def test_beam_sides_are_spatially_distinct() -> None:
    show = load_show_config()
    engine = Engine(show=show)
    snap = engine.tick(dt_s=0.0)
    view = decode_simulator_view(show, list(snap.frame))
    left = next(b for b in view.beams if b.id == "beam_left")
    right = next(b for b in view.beams if b.id == "beam_right")
    assert left.side == "left"
    assert right.side == "right"


def test_preview_speed_accelerates_clock() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.engine.set_blackout(False)
    runtime.engine.select_preset("P05", reset_clock=True)
    runtime.apply_preview_speed(60.0)
    assert runtime.preview_speed == 60.0
    before_wall = runtime.engine.clock.time()
    before_preset = runtime.engine.preset_elapsed_s
    runtime.tick(dt_s=1.0)
    # Safety/wall clock stays real-time; show clock is scaled.
    assert runtime.engine.clock.time() - before_wall == 1.0
    assert runtime.engine.preset_elapsed_s - before_preset == 60.0
    state = runtime.build_state()
    assert state.preview_speed == 60.0
    assert state.simulator.nonzero_channels >= 0


def test_accelerated_cycle_covers_full_preset_window() -> None:
    runtime = AppRuntime.create(autostart_loop=False)
    runtime.engine.set_blackout(False)
    runtime.engine.select_preset("P05", reset_clock=True)
    runtime.apply_preview_speed(60.0)
    # 180s show / 60x ≈ 3 wall seconds of tick input.
    for _ in range(3):
        runtime.tick(dt_s=1.0)
    assert runtime.engine.preset_elapsed_s >= 179.0
    snap = runtime.engine.render_at(runtime.engine.clock.time(), dt_s=0.0)
    assert snap.preset_time_s >= 0.0  # wrapped into cycle
    assert runtime.engine.preset_elapsed_s >= 180.0 or snap.preset_time_s >= 179.0
    assert runtime.build_state().simulator is not None
