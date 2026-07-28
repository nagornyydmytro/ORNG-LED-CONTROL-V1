"""RGB for every fixture type + the corrected time/speed model."""

from __future__ import annotations

from orng_led.config import default_config_dir, load_show_config
from orng_led.config.models import ChannelRole
from orng_led.engine.engine import Engine
from orng_led.engine.timing import effect_rate_hz, movement_rate_hz
from orng_led.presets.store import PresetStore
from orng_led.simulator.decode import decode_simulator_view

PRESET_IDS = tuple(f"P{i:02d}" for i in range(1, 11))


def _programs() -> dict:
    return PresetStore.load(default_config_dir() / "presets").programs()


def _engine(preset_id: str = "P05") -> Engine:
    show = load_show_config()
    engine = Engine(show=show, presets=_programs(), active_preset_id=preset_id)
    engine.set_blackout(False)
    return engine


# --- RGB ------------------------------------------------------------------


def test_every_profile_declares_rgb() -> None:
    show = load_show_config()
    for profile in show.profiles.values():
        roles = {ch.role for ch in profile.channels}
        if profile.kind.value in {"par", "face_par"}:
            assert ChannelRole.DIMMER in roles, profile.id
            assert ChannelRole.RED in roles, profile.id
        elif profile.kind.value == "bar":
            assert profile.footprint == 15, profile.id
            assert ChannelRole.SEGMENT_COLOR in roles, profile.id
            assert ChannelRole.WHOLE_COLOR in roles, profile.id
        elif profile.kind.value == "beam":
            assert profile.footprint == 13, profile.id


def test_bars_and_beams_carry_real_rgb_not_a_fixed_orange() -> None:
    show = load_show_config()
    lit_bars = 0
    for preset_id in PRESET_IDS:
        engine = _engine(preset_id)
        for _ in range(40):
            snap = engine.tick(dt_s=0.5, wall_dt_s=0.5)
            view = decode_simulator_view(show, snap.frame)
            for bar in view.bars:
                if bar.dimmer > 0.1 and any(seg > 0 for seg in bar.segments):
                    lit_bars += 1
    assert lit_bars > 10, "Bars must light via mapped segment/whole colour channels"


def test_presets_are_distinguishable_by_colour_not_only_by_speed() -> None:
    """P01–P10 must differ in palette content, and warm looks cannot own the catalogue."""
    store = PresetStore.load(default_config_dir() / "presets")
    per_preset = {
        preset_id: [ep.palette for ep in store.documents[preset_id].episodes]
        for preset_id in PRESET_IDS
    }
    cool = {"cool_blue", "mint"}
    warm = {"warm_orange", "amber", "deep_red", "white_warm"}

    # Every preset has its own palette recipe.
    assert len({tuple(palettes) for palettes in per_preset.values()}) == 10

    # Cool colour must be present in several looks, not in a single token preset.
    with_cool = [pid for pid, palettes in per_preset.items() if cool & set(palettes)]
    assert len(with_cool) >= 5, with_cool

    # …and no preset may be built from one single hue for all ten episodes.
    for preset_id, palettes in per_preset.items():
        assert len(set(palettes)) >= 2, preset_id

    # The warm family still exists (brand look), so this is diversity, not inversion.
    assert any(warm & set(palettes) for palettes in per_preset.values())


def test_no_episode_leaves_the_stage_dark() -> None:
    """Every episode of every staff preset must actually light something."""
    show = load_show_config()
    programs = _programs()
    dt = 0.2
    for preset_id in PRESET_IDS:
        engine = Engine(show=show, presets=programs, active_preset_id=preset_id)
        engine.set_blackout(False)
        peaks = [0] * 10
        for step in range(int(180.0 / dt)):
            snap = engine.tick(dt_s=dt, wall_dt_s=dt)
            episode = min(9, int(step * dt / 18.0))
            peaks[episode] = max(peaks[episode], max(snap.frame))
        for episode, peak in enumerate(peaks, start=1):
            assert peak > 10, f"{preset_id} episode {episode} renders a dark stage"


def test_blackout_zeroes_rgb_and_dimmer_for_all_kinds() -> None:
    from orng_led.engine.show_whitelist import assert_lights_dark

    show = load_show_config()
    engine = _engine("P10")
    engine.tick(dt_s=1.0, wall_dt_s=1.0)
    engine.set_blackout(True)
    snap = engine.tick(dt_s=0.1, wall_dt_s=0.1)
    view = decode_simulator_view(show, snap.frame)
    assert_lights_dark(show, snap.frame)
    for group in (view.pars, view.faces):
        assert all(p.intensity == 0 and p.r == 0 and p.g == 0 and p.b == 0 for p in group)
    assert all(b.dimmer == 0 and b.r == 0 and b.b == 0 for b in view.bars)
    assert all(b.dimmer == 0 and b.r == 0 and b.b == 0 for b in view.beams)


def test_white_hit_uses_the_rgb_model() -> None:
    show = load_show_config()
    engine = _engine("P03")
    engine.tick(dt_s=0.5, wall_dt_s=0.5)
    engine.trigger_white_hit()
    snap = engine.tick(dt_s=0.02, wall_dt_s=0.02)
    view = decode_simulator_view(show, snap.frame)
    bar = view.bars[0]
    assert bar.dimmer > 0.9
    assert any(seg > 0 for seg in bar.segments)
    pars = [p for p in view.pars if p.intensity > 0.5]
    assert pars, "White Hit must light rear PARs via mapped dimmer/red"


def test_bar_segments_are_individually_addressable() -> None:
    show = load_show_config()
    engine = _engine("P08")
    seen_uneven = False
    for _ in range(120):
        snap = engine.tick(dt_s=1 / 30, wall_dt_s=1 / 30)
        view = decode_simulator_view(show, snap.frame)
        for bar in view.bars:
            if bar.dimmer > 0.2 and max(bar.segments) - min(bar.segments) > 0.3:
                seen_uneven = True
    assert seen_uneven


# --- timing ---------------------------------------------------------------


def test_speed_maps_to_hz_not_to_episode_progress() -> None:
    assert effect_rate_hz(0.0) < effect_rate_hz(0.5) < effect_rate_hz(1.0)
    assert effect_rate_hz(0.15) < 0.35
    assert effect_rate_hz(0.85) > 2.5
    assert movement_rate_hz(1.0) < 0.25  # stays inside pan/tilt speed limits


def test_preset_speed_progression_p01_to_p10() -> None:
    programs = _programs()
    rates = {pid: programs[pid].effect_turns(180.0) / 180.0 for pid in PRESET_IDS}
    # Calm presets stay calm, hard presets are several accents per second.
    assert rates["P01"] < 0.35
    assert rates["P02"] < 0.45
    assert rates["P10"] > 2.5
    for slow, fast in (("P01", "P03"), ("P03", "P05"), ("P05", "P07"), ("P07", "P10")):
        assert rates[slow] < rates[fast], (slow, fast, rates)


def test_p10_cannot_regress_to_one_cycle_per_ten_seconds() -> None:
    """Regression guard for the original 18s-progress bug."""
    program = _programs()["P10"]
    turns_10s = program.effect_turns(10.0) - program.effect_turns(0.0)
    assert turns_10s > 20.0, turns_10s


def test_effect_phase_is_continuous_across_episode_and_loop_boundaries() -> None:
    program = _programs()["P06"]
    for boundary in (18.0, 36.0, 180.0):
        before = program.effect_turns(boundary - 0.01)
        after = program.effect_turns(boundary + 0.01)
        assert after > before
        # Continuous: the phase step matches the local rate, no random jump.
        assert after - before < 0.2, (boundary, after - before)


def test_loop_boundary_returns_to_episode_zero() -> None:
    program = _programs()["P04"]
    assert program.cycle_position(179.99).episode_index == 9
    assert program.cycle_position(180.01).episode_index == 0
    assert program.cycle_position(180.01).episode_time_s < 0.05


def test_preview_speed_does_not_accelerate_safety_timers() -> None:
    engine = _engine("P05")
    engine.strobe_press()
    # 60× preview speed for 4 real seconds: show time flies, the hold does not.
    for _ in range(120):
        engine.tick(dt_s=(1 / 30) * 60, wall_dt_s=1 / 30)
    assert engine.overlays.strobe_held is True
    assert engine.preset_elapsed_s > 200.0
    for _ in range(150):
        engine.tick(dt_s=(1 / 30) * 60, wall_dt_s=1 / 30)
    assert engine.overlays.strobe_held is False  # 8s wall-clock failsafe


def test_high_preview_speed_keeps_frames_in_bounds() -> None:
    engine = _engine("P10")
    for _ in range(400):
        snap = engine.tick(dt_s=(1 / 30) * 120, wall_dt_s=1 / 30)
        assert all(0 <= value <= 255 for value in snap.frame)
    assert engine.preset_elapsed_s > 1000.0
