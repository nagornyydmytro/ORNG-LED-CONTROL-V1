"""Auto library: canvas, compositions, real ORNG BAR frames. Not venue light."""

from __future__ import annotations

from orng_led.audio.auto_show import build_auto_stage
from orng_led.audio.gallery import catalog, render_look, render_sequence
from orng_led.audio.library import (
    BY_ID,
    COMPOSITIONS,
    FAMILIES,
    LOOKS,
    REGRESSION_VERTICAL_WAVE,
)
from orng_led.config.io import load_show_config
from orng_led.config.models import ChannelRole, FixtureKind
from orng_led.config.validation import global_channel
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.intents import BarIntent, BeamIntent, StageIntent
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view


def _paint_key(item: object) -> tuple[object, ...]:
    return (
        item.axis,
        item.direction,
        item.stagger,
        item.envelope,
        item.bands,
        round(item.tail, 3),
        item.polarity,
        item.roles,
        item.groups,
        item.needs_segments,
    )


def test_library_has_enough_families_and_distinct_compositions() -> None:
    assert len(FAMILIES) >= 20
    assert len(COMPOSITIONS) >= 100
    assert len({item.id for item in COMPOSITIONS}) == len(COMPOSITIONS)
    assert len(LOOKS) == len(COMPOSITIONS)
    families = {item.family for item in COMPOSITIONS}
    assert families == {item.id for item in FAMILIES}
    signatures = {
        (
            item.family,
            *_paint_key(item),
        )
        for item in COMPOSITIONS
    }
    assert len(signatures) == len(COMPOSITIONS)
    # Family labels must not pad the count: the painted motion has to differ.
    assert len({_paint_key(item) for item in COMPOSITIONS}) == len(COMPOSITIONS)


def test_vertical_wave_is_bottom_up_and_back_on_all_bars() -> None:
    show = load_show_config()
    fixtures = list(show.patch.fixtures)
    placements = show.layout.placements
    mid = build_auto_stage(
        fixtures,
        "orange",
        "groove",
        0.0,
        0.85,
        look=REGRESSION_VERTICAL_WAVE,
        beats=1.0,
        cycle_beats=2.0,
        placements=placements,
        groups=("bar",),
    )
    start = build_auto_stage(
        fixtures,
        "orange",
        "groove",
        0.0,
        0.85,
        look=REGRESSION_VERTICAL_WAVE,
        beats=0.0,
        cycle_beats=2.0,
        placements=placements,
        groups=("bar",),
    )
    bars_mid = [mid.fixtures[name] for name in ("bar_1", "bar_2", "bar_3", "bar_4")]
    assert all(isinstance(item, BarIntent) for item in bars_mid)
    assert all(item.whole is False for item in bars_mid)
    lit_mid = [sum(1 for bit in item.segments if bit > 0.5) for item in bars_mid]
    assert lit_mid[0] >= 6
    assert lit_mid == [lit_mid[0]] * 4
    start_bar = start.fixtures["bar_2"]
    assert isinstance(start_bar, BarIntent)
    assert sum(1 for bit in start_bar.segments if bit > 0.5) <= 2
    # Same semantic picture on every bar: not a left/right mirror law, a shared canvas.
    assert bars_mid[0].segments == bars_mid[3].segments


def test_stagger_x_bars_are_a_canvas() -> None:
    show = load_show_config()
    stage = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "groove",
        0.0,
        0.85,
        look="vertical_wave_stagger_x",
        beats=1.0,
        cycle_beats=2.0,
        placements=show.layout.placements,
        groups=("bar",),
    )
    left = stage.fixtures["bar_1"]
    right = stage.fixtures["bar_4"]
    assert isinstance(left, BarIntent) and isinstance(right, BarIntent)
    assert left.segments != right.segments


def test_directional_look_is_not_required_to_mirror() -> None:
    show = load_show_config()
    stage = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "groove",
        0.0,
        0.85,
        look="cascade_left",
        beats=1.0,
        cycle_beats=2.0,
        placements=show.layout.placements,
        groups=("bar",),
    )
    left = stage.fixtures["bar_1"]
    right = stage.fixtures["bar_4"]
    assert isinstance(left, BarIntent) and isinstance(right, BarIntent)
    assert left.segments != right.segments


def test_invert_segments_is_renderer_only() -> None:
    show = load_show_config()
    fixtures = list(show.patch.fixtures)
    stage = build_auto_stage(
        fixtures,
        "orange",
        "groove",
        0.0,
        0.85,
        look=REGRESSION_VERTICAL_WAVE,
        beats=0.7,
        cycle_beats=2.0,
        placements=show.layout.placements,
        groups=("bar",),
    )
    pose = {
        fixture.id: BeamMotionState(
            pan=float(fixture.spatial.home_pan), tilt=float(fixture.spatial.home_tilt)
        )
        for fixture in fixtures
        if fixture.kind is FixtureKind.BEAM
    }
    parked = render_stage(show, StageIntent(), pose)
    plain = render_stage(show, stage, pose)
    bar = show.patch.fixture("bar_4")
    previous = bar.spatial.invert_segments
    bar.spatial.invert_segments = True
    try:
        flipped = render_stage(show, stage, pose)
    finally:
        bar.spatial.invert_segments = previous
    bottom = _at(show, bar, ChannelRole.SEGMENT_COLOR, 1)
    top = _at(show, bar, ChannelRole.SEGMENT_COLOR, 8)
    assert plain[bottom] != plain[top] or plain[bottom] > 0
    assert flipped[top] == plain[bottom]
    assert flipped[bottom] == plain[top]
    for fixture in fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        for role in (ChannelRole.PAN_COARSE, ChannelRole.TILT_COARSE):
            index = _at(show, fixture, role)
            if index is None:
                continue
            assert plain[index] == parked[index]
    view = decode_simulator_view(show, plain)
    faces = {item.id: item.intensity for item in view.faces}
    assert faces["face_par_1"] == 0.0 and faces["face_par_2"] == 0.0
    for intent in stage.fixtures.values():
        if isinstance(intent, BeamIntent):
            assert intent.pan is None and intent.tilt is None and intent.strobe == 0.0
            assert intent.dimmer in {0.0, 1.0}


def test_hit_decay_uses_dimmer_not_a_held_level() -> None:
    show = load_show_config()
    peak = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "groove",
        0.0,
        1.0,
        look="accent",
        beats=0.0,
        cycle_beats=1.0,
        placements=show.layout.placements,
    )
    later = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "groove",
        0.0,
        1.0,
        look="accent",
        beats=0.45,
        cycle_beats=1.0,
        placements=show.layout.placements,
    )
    first = peak.fixtures["par_1"].intensity
    second = later.fixtures["par_1"].intensity
    assert first > 0.7
    assert second < first * 0.4


def test_explicit_look_does_not_expand_to_wash() -> None:
    from orng_led.audio.library import resolve_composition
    from orng_led.config.models import FixtureKind

    class _Lamp:
        def __init__(self, kind: FixtureKind) -> None:
            self.kind = kind

    chosen = resolve_composition("hit_pars", 0, "orange", [_Lamp(FixtureKind.BAR)])
    assert chosen.id == "hit_pars"
    chosen = resolve_composition("vertical_wave", 0, "heads", [_Lamp(FixtureKind.PAR)])
    assert chosen.id == "vertical_wave"


def test_family_representatives_move_differently_on_orng_bar() -> None:
    show = load_show_config()
    fingerprints: dict[str, tuple[tuple[int, ...], ...]] = {}
    for family in FAMILIES:
        item = next(comp for comp in COMPOSITIONS if comp.family == family.id)
        frames = render_sequence(item.id, cycle_beats=2.0, palette="orange", frames=8, show=show)
        bits = tuple(
            (
                tuple(
                    (round(bar["dimmer"], 2),)
                    + tuple(1 if bit > 0.05 else 0 for bit in bar["segments"])
                    for bar in frame["bars"]
                ),
                tuple(round(par["intensity"], 2) for par in frame["pars"]),
            )
            for frame in frames
        )
        fingerprints[family.id] = bits
        lit = any(any(frame) for frame in bits) or any(
            par["intensity"] > 0.05 for par in frames[3]["pars"]
        )
        assert lit, family.id
        assert frames[0]["face_dark"] is True
        assert frames[0]["beams_parked"] is True
    unique = set(fingerprints.values())
    assert len(unique) >= len(FAMILIES) - 2


def test_directed_sequences_change_family_not_only_palette() -> None:
    from orng_led.audio.planner import EpisodePlanner

    for seed, character in ((1, "rhythmic"), (7, "smooth"), (11, "impact")):
        planner = EpisodePlanner(seed=seed)
        looks: list[str] = []
        families: list[str] = []
        beat = 0.0
        for _ in range(8):
            episode = planner.advance(
                beat,
                section="groove",
                level=0.85,
                theme="orange",
                locked=True,
                character=character,
            )
            looks.append(episode.look)
            families.append(BY_ID[episode.look].family)
            beat += episode.duration_beats
        assert looks[0] != looks[1], (character, looks)
        assert len(set(looks)) >= 2, (character, looks)
        assert len(set(families)) <= len(set(looks)), (character, families)


def test_incompatible_heads_theme_skips_segment_looks() -> None:
    show = load_show_config()
    stage = build_auto_stage(
        list(show.patch.fixtures),
        "heads",
        "groove",
        0.2,
        0.8,
        look="vertical_wave",
        placements=show.layout.placements,
        groups=("par", "beam"),
    )
    assert all(not fixture_id.startswith("bar") for fixture_id in stage.fixtures)
    assert "face_par_1" not in stage.fixtures


def test_gallery_uses_real_renderer_and_keeps_face_dark() -> None:
    data = catalog()
    assert data["regression"] == "vertical_wave"
    assert len(data["compositions"]) >= 100
    show = load_show_config()
    frame = render_look("vertical_wave", beats=1.0, cycle_beats=2.0, palette="orange", show=show)
    assert frame["face_dark"] is True
    assert frame["beams_parked"] is True
    assert len(frame["bars"]) == 4
    lit = sum(1 for bit in frame["bars"][0]["segments"] if bit > 0.05)
    assert lit >= 6
    sequence = render_sequence(
        "vertical_wave", cycle_beats=2.0, palette="orange", frames=8, show=show
    )
    counts = [sum(1 for bit in item["bars"][1]["segments"] if bit > 0.05) for item in sequence]
    assert max(counts) > min(counts)


def _at(show: object, fixture: object, role: ChannelRole, segment: int | None = None) -> int | None:
    profile = show.profile_for(fixture)
    for channel in profile.channels:
        if channel.role is not role:
            continue
        if segment is not None and channel.segment_index != segment:
            continue
        return global_channel(fixture.start_address, channel.local) - 1
    return None
