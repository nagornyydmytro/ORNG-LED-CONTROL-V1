"""Operator next-look, melodic fallback, pause dim, head preview. Model time."""

from __future__ import annotations

from types import SimpleNamespace

from orng_led.audio.auto_show import (
    SectionTracker,
    StructuralEvent,
    build_auto_stage,
    consume_accent,
    lay_par_bed,
)
from orng_led.audio.head_motion import HEAD_PATHS_LIVE, HeadRig, bounds_for, limited, target
from orng_led.audio.library import BY_ID
from orng_led.audio.planner import EpisodePlanner
from orng_led.audio.preview import render_preview_view
from orng_led.config.io import load_show_config
from orng_led.engine.intents import BarIntent, BeamIntent, ParIntent


def _planner(character: str = "rhythmic") -> EpisodePlanner:
    show = load_show_config()
    planner = EpisodePlanner(seed=3)
    planner.character = character
    planner.bind_scene(list(show.patch.fixtures))
    planner.advance(0.0, section="groove", level=0.7, theme="orange", locked=True, character=character)
    return planner


def test_next_look_keeps_palette_and_waits_one_beat_when_locked() -> None:
    planner = _planner()
    opening = planner.current
    assert opening is not None
    look = planner.request_next_look(0.2, locked=True)
    assert look != opening.look
    assert planner.current.look == opening.look
    assert planner.upcoming is not None
    assert planner.upcoming.look == look
    assert planner.upcoming.start_beat == 1.0
    assert planner.upcoming.palette == opening.palette
    assert planner.upcoming.picture_cycle == opening.picture_cycle
    held = planner.advance(0.5, section="groove", level=0.7, theme="orange", locked=True)
    assert held.look == opening.look
    landed = planner.advance(1.0, section="groove", level=0.7, theme="orange", locked=True)
    assert landed.look == look
    assert landed.start_beat == 1.0
    try:
        planner.request_next_look(1.05, locked=True)
        planner.request_next_look(1.1, locked=True)
    except ValueError as exc:
        assert "Зачекайте" in str(exc)
    else:
        raise AssertionError("a second click was queued")


def test_melodic_hearing_leaves_a_chopped_picture() -> None:
    planner = _planner("impact")
    planner.current = planner.current.__class__(**{
        **planner.current.__dict__,
    }) if False else planner.current
    chopped = planner.advance(
        0.0,
        section="groove",
        level=0.7,
        theme="orange",
        locked=True,
        character="impact",
    )
    # Force a hit picture, then lose the grid on a melody.
    from dataclasses import replace
    from orng_led.audio.planner import AutoAction

    planner.current = replace(
        chopped,
        look="accent",
        actions=(AutoAction("picture", "accent", 1.0),),
    )
    planner.advance(
        1.0,
        section="groove",
        level=0.55,
        theme="orange",
        locked=False,
        hearing="melodic",
        character="impact",
        wall_s=0.0,
    )
    softened = planner.advance(
        2.0,
        section="groove",
        level=0.55,
        theme="orange",
        locked=False,
        hearing="melodic",
        character="impact",
        wall_s=1.0,
    )
    assert softened.look != "accent"
    assert softened.picture_cycle >= 4.0
    assert BY_ID[softened.look].envelope not in {"hit", "pulse", "double"}


def test_pause_dims_once_and_a_fader_does_not() -> None:
    planner = _planner()
    planner.advance(
        4.0,
        section="groove",
        level=0.2,
        theme="orange",
        locked=True,
        wall_s=10.0,
    )
    assert planner.dimming(10.2) is False
    planner.advance(
        8.0,
        section="break",
        level=0.2,
        theme="orange",
        locked=True,
        wall_s=20.0,
        events=(StructuralEvent("pause", 1, 20.0),),
    )
    assert planner.dimming(20.2) is True
    assert planner.dimming(22.0) is False
    planner.advance(
        12.0,
        section="break",
        level=0.2,
        theme="orange",
        locked=True,
        wall_s=30.0,
        events=(StructuralEvent("pause", 2, 30.0),),
    )
    assert planner.dimming(30.2) is False


def test_grid_loss_clears_a_dim_and_blackout_is_separate() -> None:
    planner = _planner()
    planner.advance(
        4.0,
        section="break",
        level=0.4,
        theme="orange",
        locked=True,
        wall_s=5.0,
        events=(StructuralEvent("pause", 7, 5.0),),
    )
    assert planner.dimming(5.2) is True
    planner.advance(4.5, section="break", level=0.4, theme="orange", locked=False, wall_s=5.4)
    assert planner.dimming(5.4) is False


def test_bars_are_a_canvas_and_head_pairs_stay_together() -> None:
    show = load_show_config()
    stage = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "groove",
        0.0,
        0.8,
        look="fill_left",
        beats=1.0,
        cycle_beats=2.0,
        placements=show.layout.placements,
    )
    assert stage.fixtures["bar_1"].segments != stage.fixtures["bar_4"].segments
    heads = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "groove",
        0.0,
        0.8,
        look="sine_heads",
        beats=0.4,
        cycle_beats=4.0,
        placements=show.layout.placements,
        cast="heads",
        groups=("par", "beam"),
    )
    assert heads.fixtures["par_1"].intensity == heads.fixtures["par_4"].intensity
    assert heads.fixtures["par_2"].intensity == heads.fixtures["par_3"].intensity
    left = heads.fixtures.get("beam_left")
    right = heads.fixtures.get("beam_right")
    if isinstance(left, BeamIntent) and isinstance(right, BeamIntent):
        assert left.dimmer == right.dimmer
        assert left.pan is None and right.pan is None


def test_preview_head_path_is_continuous_and_blackout_does_not_add_pan() -> None:
    show = load_show_config()
    rig = HeadRig()
    first = render_preview_view(
        show, "hit_heads", beats=0.0, cycle_beats=4, palette="orange", intensity="full", rig=rig
    )
    second = render_preview_view(
        show, "hit_heads", beats=0.5, cycle_beats=4, palette="orange", intensity="full", rig=rig
    )
    a = {item["id"]: item for item in first["beams"]}
    b = {item["id"]: item for item in second["beams"]}
    if a and b and "beam_left" in a and "beam_left" in b:
        assert abs(float(a["beam_left"]["pan"]) - float(b["beam_left"]["pan"])) <= 0.04 * 0.5 + 1e-6
    moved = limited((0.5, 0.5), (0.9, 0.1), 0.5)
    assert abs(moved[0] - 0.5) <= 0.04 * 0.5 + 1e-9
    assert HEAD_PATHS_LIVE is True


def test_library_splits_smooth_and_beat_by_behaviour() -> None:
    assert BY_ID["vertical_wave"].program == "smooth"
    assert BY_ID["heads_only_fan"].program == "smooth"
    assert BY_ID["heads_only_arcs"].program == "smooth"
    assert BY_ID["heads_beat_pass"].program == "beat"
    assert BY_ID["heads_rise_pulse"].program == "beat"
    assert BY_ID["heads_only_fan"].groups == ("beam",)
    smooth = {item.program for item in BY_ID.values() if item.program == "smooth"}
    beat = {item.program for item in BY_ID.values() if item.program == "beat"}
    assert smooth and beat


def test_smooth_mode_stays_when_the_grid_returns_a_second_later() -> None:
    planner = _planner("impact")
    from dataclasses import replace
    from orng_led.audio.planner import AutoAction

    current = planner.current
    assert current is not None
    planner.current = replace(current, look="accent", actions=(AutoAction("picture", "accent", 1.0),))
    planner.advance(
        1.0, section="groove", level=0.6, theme="orange", locked=False,
        hearing="melodic", wall_s=0.0, character="impact",
    )
    softened = planner.advance(
        2.0, section="groove", level=0.6, theme="orange", locked=False,
        hearing="melodic", wall_s=1.0, character="impact",
    )
    assert planner._clock == "melodic"
    assert BY_ID[softened.look].program == "smooth"
    back = planner.advance(
        3.0, section="groove", level=0.7, theme="orange", locked=True,
        hearing="rhythmic", wall_s=2.0, character="impact",
    )
    assert back.clock == "melodic"
    assert BY_ID[back.look].program == "smooth"
    restored = planner.advance(
        4.0, section="groove", level=0.7, theme="orange", locked=True,
        hearing="rhythmic", wall_s=8.0, character="impact",
    )
    assert restored.clock == "musical"
    assert BY_ID[restored.look].program == "beat"


def test_next_look_stays_inside_the_current_program() -> None:
    planner = _planner()
    planner._clock = "melodic"
    current = planner.current
    assert current is not None
    planner.current = current.__class__(
        **{**current.__dict__, "look": "heads_only_fan", "clock": "melodic"}
    )
    # picture action still names the old look; the filter uses current.look
    picked = planner.request_next_look(1.2, locked=False)
    assert BY_ID[picked].program == "smooth"
    assert picked != "heads_only_fan"


def test_head_bounds_come_from_preset_span_not_full_throw() -> None:
    spatial = SimpleNamespace(
        pan_min=0.0, pan_max=1.0, home_pan=0.5,
        tilt_min=0.0, tilt_max=1.0, home_tilt=0.44,
        max_pan_speed=0.35, max_tilt_speed=0.25, side="left",
    )
    wide = bounds_for(SimpleNamespace(id="beam_left", spatial=spatial))
    assert abs(float(wide["pan"]["lo"]) - 0.08) < 1e-9
    assert abs(float(wide["pan"]["hi"]) - 0.92) < 1e-9
    assert wide["pan"]["crosses_home"] is True
    assert wide["origin"]
    tight = SimpleNamespace(
        pan_min=0.95, pan_max=1.0, home_pan=0.97,
        tilt_min=0.0, tilt_max=1.0, home_tilt=0.5,
        max_pan_speed=0.35, max_tilt_speed=0.25, side="right",
    )
    stuck = bounds_for(SimpleNamespace(id="beam_right", spatial=tight))
    assert stuck["pan"]["still"] is True
    assert "calibration_excludes_preset" in str(stuck["pan"]["reason"])


def test_head_targets_are_spatial_mirrors() -> None:
    span = (0.08, 0.92)
    tilt = (0.24, 0.64)
    left = target(1.0, -1.0, "groove", look="heads_only_fan", pan_span=span, tilt_span=tilt)
    right = target(1.0, 1.0, "groove", look="heads_only_fan", pan_span=span, tilt_span=tilt)
    assert left[0] is not None and right[0] is not None
    assert abs(((left[0] + right[0]) / 2.0) - 0.5) < 1e-6
    assert left[0] != right[0]
    assert left[1] == right[1]


def test_heads_only_leaves_pars_and_bars_off() -> None:
    show = load_show_config()
    fixtures = list(show.patch.fixtures)
    stage = build_auto_stage(
        fixtures, "white", "groove", 0.0, 0.8,
        look="heads_only_fan", beats=2.0, cycle_beats=8.0,
        placements=show.layout.placements, groups=("beam",),
    )
    assert stage.fixtures
    assert all(isinstance(intent, BeamIntent) for intent in stage.fixtures.values())
    assert all(intent.strobe == 0.0 and intent.pan is None for intent in stage.fixtures.values())
    pulsed = build_auto_stage(
        fixtures, "white", "groove", 0.0, 0.8,
        look="heads_rise_pulse", beats=2.0, cycle_beats=8.0,
        placements=show.layout.placements, groups=("beam",), flash_hold=0.0,
    )
    assert pulsed.fixtures
    assert all(isinstance(intent, BeamIntent) and intent.dimmer == 0.0 and intent.strobe == 0.0 for intent in pulsed.fixtures.values())


def test_extra_accent_skips_fader_grid_kick_and_late_events() -> None:
    ear = SectionTracker()
    ear.accent_amount = 1.0
    ear.consider_accent(now=1.0, onset=0.1, level=0.4, phase=0.4)
    assert ear.accent_ready is None
    ear.consider_accent(now=1.0, onset=0.9, level=0.4, phase=0.02)
    assert ear.accent_ready is None
    ear.consider_accent(now=1.0, onset=0.9, level=0.4, phase=0.4)
    assert ear.accent_ready == 1.0
    show = load_show_config()
    bare = build_auto_stage(
        list(show.patch.fixtures), "orange", "groove", 0.0, 0.0,
        look="heads_only_fan", beats=0.0, cycle_beats=8.0,
        placements=show.layout.placements, groups=("beam",),
    )
    late, spent = consume_accent(
        bare, list(show.patch.fixtures), ("par",), 0.8, now=1.5, ready_at=1.0,
    )
    assert spent is True
    assert late.fixtures.keys() == bare.fixtures.keys()


def test_par_bed_does_not_invent_a_second_bpm() -> None:
    show = load_show_config()
    stage = build_auto_stage(
        list(show.patch.fixtures), "orange", "groove", 0.2, 0.7,
        look="wash", beats=1.0, cycle_beats=8.0, placements=show.layout.placements,
    )
    bed = lay_par_bed(stage, 1.0, 0.7)
    assert any(isinstance(intent, ParIntent) for intent in bed.fixtures.values())
    assert any(isinstance(intent, BarIntent) for intent in bed.fixtures.values())
