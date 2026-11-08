"""Reproduced planner, layer, and head-gate failures. Output stays disarmed."""

from __future__ import annotations

import itertools

from orng_led.audio.auto_show import StructuralEvent, stack_auto_layers
from orng_led.audio.head_motion import HeadRig, HeadTravel
from orng_led.audio.library import BY_ID, COMPOSITIONS
from orng_led.audio.planner import EpisodePlanner
from orng_led.audio.settings import groups_cover
from orng_led.config.io import load_show_config
from orng_led.config.models import FixtureKind
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.intents import BeamIntent, Rgbw
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view


def _fixtures(show):
    return list(show.patch.fixtures)


def _decoded(show, stage, rig: HeadRig | None = None):
    motion = {}
    if rig is not None:
        for fixture in show.patch.fixtures:
            if fixture.kind is not FixtureKind.BEAM:
                continue
            pose = rig.pose(fixture.id)
            if pose is not None:
                motion[fixture.id] = BeamMotionState(pan=pose[0], tilt=pose[1])
    return decode_simulator_view(show, render_stage(show, stage, motion))


def _walk_until(planner: EpisodePlanner, show, limit: float):
    step = 0
    t = 0.17
    last = None
    while t <= limit + 1e-9:
        locked = t < 1.0 or t >= 10.0
        last = planner.advance(
            2.0 * t,
            section="groove",
            level=0.8,
            theme="orange",
            locked=locked,
            hearing="rhythmic",
            wall_s=t,
        )
        step += 1
        t = 0.17 + step / 30.0
    return last, t


def _walk_restore(planner: EpisodePlanner, show) -> EpisodePlanner:
    planner.bind_scene(_fixtures(show))
    step = 0
    t = 0.17
    while t <= 40.0 + 1e-9:
        locked = t < 1.0 or t >= 10.0
        planner.advance(
            2.0 * t,
            section="groove",
            level=0.8,
            theme="orange",
            locked=locked,
            hearing="rhythmic",
            wall_s=t,
        )
        step += 1
        t = 0.17 + step / 30.0
    return planner


def test_grid_return_leaves_the_smooth_clock() -> None:
    show = load_show_config()
    planner = _walk_restore(EpisodePlanner(seed=7), show)
    current = planner.current
    assert current is not None
    assert planner._clock == "musical"
    assert current.clock == "musical"
    assert current.look
    assert not (planner.upcoming is not None and planner.upcoming.reason == "restore" and planner.upcoming.start_beat < 80)
    view = _decoded(show, _picture(show, current.look, 0.8))
    assert any(par.intensity > 0.05 for par in view.pars) or any(bar.dimmer > 0.05 for bar in view.bars)


def test_restore_survives_a_fraction_and_a_one_frame_boundary() -> None:
    show = load_show_config()
    for limit in (12.02, 12.17, 12.48):
        planner = EpisodePlanner(seed=7)
        planner.bind_scene(_fixtures(show))
        _walk_until(planner, show, limit)
        pending = planner.upcoming
        assert planner._clock == "melodic"
        assert pending is not None and pending.reason == "restore"
        phase = planner.current.actions[0].origin_beats if planner.current and planner.current.actions else 0.0
        planner.advance(
            pending.start_beat + 0.02,
            section="groove",
            level=0.8,
            theme="orange",
            locked=True,
            hearing="rhythmic",
            wall_s=limit + 1.0,
        )
        assert planner._clock == "musical"
        assert planner.current is not None and planner.current.clock == "musical"
        assert planner.current.actions
        assert planner.current.actions[0].origin_beats == phase


def test_losing_the_grid_again_drops_a_pending_restore() -> None:
    show = load_show_config()
    planner = EpisodePlanner(seed=7)
    planner.bind_scene(_fixtures(show))
    _walk_until(planner, show, 12.1)
    assert planner.upcoming is not None and planner.upcoming.reason == "restore"
    planner.advance(24.4, section="groove", level=0.8, theme="orange", locked=False, hearing="rhythmic", wall_s=12.4)
    assert planner.upcoming is None or planner.upcoming.reason != "restore"
    assert planner._clock != "musical" or planner.current is None or planner.current.reason != "restore"


def test_a_fresh_drop_is_not_held_by_the_smooth_clock_and_an_old_one_does_not_replay() -> None:
    show = load_show_config()
    planner = EpisodePlanner(seed=7)
    planner.bind_scene(_fixtures(show))
    opened, _t = _walk_until(planner, show, 12.1)
    assert planner._clock == "melodic"
    assert opened is not None
    drop = StructuralEvent("drop", 7, 12.2)
    armed = planner.advance(
        24.3,
        section="drop",
        level=0.9,
        theme="orange",
        locked=True,
        hearing="rhythmic",
        wall_s=12.3,
        events=(drop,),
    )
    assert planner.upcoming is not None and planner.upcoming.reason == "drop_next"
    assert planner.upcoming.start_beat <= 24.3 + 2.0
    landed = planner.advance(
        planner.upcoming.start_beat + 0.05,
        section="drop",
        level=0.9,
        theme="orange",
        locked=True,
        hearing="rhythmic",
        wall_s=13.0,
        events=(drop,),
    )
    assert landed.reason == "drop_next" or landed.role == "accent"
    assert landed.start_beat <= 24.3 + 2.0
    assert planner._clock == "musical"
    ident = landed.ident
    again = planner.advance(
        landed.start_beat + 4.0,
        section="groove",
        level=0.8,
        theme="orange",
        locked=True,
        hearing="rhythmic",
        wall_s=18.0,
        events=(drop,),
    )
    assert again.ident == ident or again.reason != "drop_next"


def test_par_only_picks_a_par_picture_and_empty_stays_dark() -> None:
    show = load_show_config()
    classes = ("par", "bar", "beam")
    for mask in itertools.product((False, True), repeat=3):
        enabled = tuple(name for name, on in zip(classes, mask) if on)
        planner = EpisodePlanner(seed=7)
        planner.bind_scene(_fixtures(show))
        planner.set_groups(enabled)
        episode = planner.advance(0.0, section="groove", level=0.8, theme="orange", locked=True, wall_s=0.0)
        if not enabled:
            assert episode.look == ""
            assert episode.groups == ()
            continue
        item = BY_ID[episode.look]
        assert groups_cover(item.groups, set(enabled))
        assert episode.look != "wash" or set(item.groups) <= set(enabled)
        for action in episode.actions:
            assert groups_cover(BY_ID[action.look].groups, set(enabled))
    planner = EpisodePlanner(seed=7)
    planner.set_groups(("par",))
    episode = planner.advance(0.0, section="groove", level=0.8, theme="orange", locked=True, wall_s=0.0)
    stage = _picture(show, episode.look, 0.8, groups=episode.groups)
    view = _decoded(show, stage)
    assert any(par.intensity > 0.02 for par in view.pars)
    assert all(bar.dimmer <= 0.02 for bar in view.bars)


def test_an_excluded_composition_does_not_return_as_a_bed(tmp_path) -> None:
    show = load_show_config()
    planner = EpisodePlanner(seed=7)
    planner.bind_scene(_fixtures(show))
    planner.set_excluded(("breathe_pars",))
    episode = planner.advance(1.0, section="groove", level=0.8, theme="orange", locked=True, wall_s=1.0)
    assert all(action.look != "breathe_pars" for action in episode.actions)
    planner.set_excluded(tuple(item.id for item in COMPOSITIONS))
    dark = planner.advance(40.0, section="groove", level=0.8, theme="orange", locked=True, wall_s=20.0)
    assert dark.look == "" or dark.look not in planner.excluded
    assert all(action.look not in planner.excluded for action in dark.actions)
    from orng_led.audio.auto_show import SliceHearing
    from orng_led.audio.service import BpmService

    service = BpmService(tmp_path, "lab")
    service.configure(
        enabled=True,
        scope="auto",
        palette="orange",
        par_rhythm=True,
        excluded=tuple(item.id for item in COMPOSITIONS),
    )
    service.follower.bpm = 120.0
    service.follower.locked = True
    service.follower._started = True
    service.follower._fresh_until = service._now + 30.0
    service._push_section(
        SliceHearing(0.4, 0.5, low_attacks=(0.0,), high_attacks=(0.05, 0.22), generation=1),
        0.4,
        service._now,
        0.5,
    )
    service.section.shown = 0.85
    service.section.section = "groove"
    stage = service.look(list(show.patch.fixtures), show.layout.placements)
    episode = service.planner.current
    assert episode is not None
    assert episode.look == ""
    assert episode.groups == ()
    view = _decoded(show, stage)
    assert view.pars
    assert all(par.intensity <= 0.02 for par in view.pars)


def test_accent_does_not_relight_a_dark_head_return() -> None:
    show = load_show_config()
    travel = HeadTravel(
        scene_id="course",
        revision="course",
        spans={fixture.id: (0.08, 0.92, 0.24, 0.76) for fixture in show.patch.fixtures if fixture.kind is FixtureKind.BEAM},
    )
    rig = HeadRig()
    from orng_led.audio.auto_show import build_auto_stage
    from orng_led.audio.head_motion import preview_heads

    stage = None
    beat = 0.0
    while beat <= 5.0 + 1e-9:
        bare = build_auto_stage(
            _fixtures(show),
            "orange",
            "groove",
            0.0,
            0.8,
            look="heads_only_rise",
            beats=beat,
            cycle_beats=1.0,
            placements=show.layout.placements,
            groups=("beam",),
        )
        stage = preview_heads(
            show,
            bare,
            beats=beat,
            section="groove",
            look="heads_only_rise",
            beat_period_s=0.5,
            rig=rig,
            travel=travel,
            cycle_beats=1.0,
        )
        beat += 0.25
    assert stage is not None
    assert rig.unlit
    for ident in ("beam_left", "beam_right"):
        intent = stage.fixtures[ident]
        assert isinstance(intent, BeamIntent)
        assert intent.dimmer == 0.0
    stacked, _spent = stack_auto_layers(
        stage,
        _fixtures(show),
        amount=0.8,
        color=Rgbw(r=1.0, g=0.4, b=0.0, w=0.0),
        cast_groups=("par", "bar", "beam"),
        own_par=False,
        par_bed=True,
        pulse=False,
        wall_s=2.5,
        beat_phase=0.2,
        accent=1.0,
        accent_ready=2.5,
        now=2.5,
        flash=1.0,
        head_dark=rig.unlit,
    )
    view = _decoded(show, stacked, rig)
    for beam in view.beams:
        if beam.id in rig.unlit:
            assert beam.dimmer == 0.0


def _picture(show, look: str, amount: float, groups: tuple[str, ...] | None = None):
    from orng_led.audio.auto_show import build_auto_stage

    owned = groups if groups is not None else (BY_ID[look].groups if look in BY_ID else ())
    return build_auto_stage(
        _fixtures(show),
        "orange",
        "groove",
        0.25,
        amount,
        look=look or None,
        beats=0.25,
        cycle_beats=4.0,
        placements=show.layout.placements,
        groups=owned,
    )
