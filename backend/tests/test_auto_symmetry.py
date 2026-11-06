"""Auto canvas on the real ORNG BAR plan. Mirror is optional. No Art-Net."""

from __future__ import annotations

import pytest

from orng_led.audio.auto_show import build_auto_stage
from orng_led.audio.library import REGRESSION_VERTICAL_WAVE
from orng_led.audio.symmetry import resolve_mirrors
from orng_led.config.io import load_show_config
from orng_led.config.models import ChannelRole, FixtureKind
from orng_led.config.validation import global_channel
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.intents import BarIntent, BeamIntent, StageIntent
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view

# Audience axis x=0.5. Pairs are the reflections in layout.yaml, not patch order.
EXPECTED_PAIRS = {
    ("par_1", "par_4"),
    ("par_2", "par_3"),
    ("beam_left", "beam_right"),
    ("bar_1", "bar_4"),
    ("bar_2", "bar_3"),
    ("face_par_1", "face_par_2"),
}


class _Lamp:
    def __init__(self, lamp_id: str, kind: FixtureKind, x: float, y: float, z: float = 0.8) -> None:
        self.id = lamp_id
        self.kind = kind
        self.layout_x = x
        self.layout_y = y
        self.layout_z = z
        self.spatial = type("S", (), {"segment_count": 8, "invert_segments": False, "order": 1})()


def test_orng_bar_pairs_come_from_the_plan() -> None:
    show = load_show_config()
    mirror = resolve_mirrors(list(show.patch.fixtures), show.layout.placements)
    assert {(item.left_id, item.right_id) for item in mirror.pairs} == EXPECTED_PAIRS
    assert mirror.excluded == ()
    assert mirror.ambiguous == ()
    assert mirror.unplaced == ()
    assert mirror.centers == ()
    bars = [item for item in mirror.pairs if item.kind is FixtureKind.BAR]
    assert [item.left_id for item in bars] == ["bar_2", "bar_1"]
    assert bars[0].distance < bars[1].distance


def test_incomplete_and_ambiguous_fixtures_are_not_invented() -> None:
    kind = FixtureKind.PAR
    lamps = [
        _Lamp("kept-l", kind, 0.26, 0.74),
        _Lamp("kept-r", kind, 0.74, 0.74),
        _Lamp("alone", kind, 0.10, 0.20),
        _Lamp("clash-a", kind, 0.20, 0.40),
        _Lamp("clash-b", kind, 0.80, 0.40),
        _Lamp("clash-c", kind, 0.80, 0.40),
        _Lamp("center", kind, 0.50, 0.50),
    ]
    mirror = resolve_mirrors(lamps)
    assert {(item.left_id, item.right_id) for item in mirror.pairs} == {("kept-l", "kept-r")}
    assert mirror.excluded == ("alone",)
    assert mirror.centers == ("center",)
    assert any(set(group) == {"clash-a", "clash-b", "clash-c"} for group in mirror.ambiguous)
    lamps.append(_Lamp("lost", kind, None, None, None))  # type: ignore[arg-type]
    stage = build_auto_stage(lamps, "orange", "groove", 0.1, 0.8, look="wash")
    assert "lost" not in stage.fixtures
    assert "kept-l" in stage.fixtures and "kept-r" in stage.fixtures
    assert "center" in stage.fixtures
    assert "alone" in stage.fixtures


def test_optional_symmetry_is_not_the_default_law() -> None:
    show = load_show_config()
    fixtures = list(show.patch.fixtures)
    placements = show.layout.placements
    mirrored = build_auto_stage(
        fixtures, "orange", "groove", 0.2, 0.8, look="breathe_mirror", placements=placements
    )
    _assert_pairs(mirrored)
    wave = build_auto_stage(
        fixtures,
        "orange",
        "groove",
        0.0,
        0.8,
        look=REGRESSION_VERTICAL_WAVE,
        beats=0.7,
        cycle_beats=2.0,
        placements=placements,
        groups=("bar",),
    )
    assert "face_par_1" not in wave.fixtures and "face_par_2" not in wave.fixtures
    for fixture in fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        intent = wave.fixtures.get(fixture.id)
        if intent is None:
            continue
        assert isinstance(intent, BeamIntent)
        assert intent.pan is None and intent.tilt is None and intent.strobe == 0.0
        assert intent.dimmer in {0.0, 1.0}
    bloom = build_auto_stage(
        fixtures, "orange", "groove", 0.25, 0.8, look="bloom", beats=0.5, cycle_beats=2.0, placements=placements
    )
    inner = bloom.fixtures["bar_2"]
    outer = bloom.fixtures["bar_1"]
    assert isinstance(inner, BarIntent) and isinstance(outer, BarIntent)
    assert inner.segments == bloom.fixtures["bar_3"].segments
    assert outer.segments == bloom.fixtures["bar_4"].segments


def test_decoded_frames_stay_mirrored_when_one_bar_is_inverted() -> None:
    show = load_show_config()
    fixtures = list(show.patch.fixtures)
    stage = build_auto_stage(
        fixtures,
        "orange",
        "groove",
        0.25,
        0.8,
        look=REGRESSION_VERTICAL_WAVE,
        beats=0.7,
        cycle_beats=2.0,
        placements=show.layout.placements,
        groups=("bar",),
    )
    pose = {
        fixture.id: BeamMotionState(
            pan=float(fixture.spatial.home_pan),
            tilt=float(fixture.spatial.home_tilt),
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
        view = decode_simulator_view(show, flipped)
    finally:
        bar.spatial.invert_segments = previous
    bars = {item.id: item for item in view.bars}
    assert bars["bar_1"].segments == bars["bar_4"].segments
    assert bars["bar_2"].segments == bars["bar_3"].segments
    assert bars["bar_1"].dimmer == pytest.approx(bars["bar_4"].dimmer)
    raw_bottom = _at(show, show.patch.fixture("bar_4"), ChannelRole.SEGMENT_COLOR, 1)
    raw_top = _at(show, show.patch.fixture("bar_4"), ChannelRole.SEGMENT_COLOR, 8)
    assert plain[raw_bottom] > 0
    assert flipped[raw_top] == plain[raw_bottom]
    assert flipped[raw_bottom] == plain[raw_top]
    for left_id, right_id in (("par_1", "par_4"), ("par_2", "par_3")):
        left = show.patch.fixture(left_id)
        right = show.patch.fixture(right_id)
        assert plain[_at(show, left, ChannelRole.DIMMER)] == plain[_at(show, right, ChannelRole.DIMMER)]
    assert plain[_at(show, show.patch.fixture("par_2"), ChannelRole.RED)] == plain[
        _at(show, show.patch.fixture("par_3"), ChannelRole.RED)
    ]
    for fixture in fixtures:
        if fixture.kind is not FixtureKind.BEAM:
            continue
        for role in (ChannelRole.PAN_COARSE, ChannelRole.TILT_COARSE):
            index = _at(show, fixture, role)
            if index is None:
                continue
            assert plain[index] == parked[index]
    decoded = decode_simulator_view(show, plain)
    pars = {item.id: item for item in decoded.pars}
    assert pars["par_1"].intensity == pytest.approx(pars["par_4"].intensity)
    assert (pars["par_1"].r, pars["par_1"].g, pars["par_1"].b) == (
        pars["par_4"].r,
        pars["par_4"].g,
        pars["par_4"].b,
    )
    faces = {item.id: item.intensity for item in decoded.faces}
    assert faces["face_par_1"] == 0.0 and faces["face_par_2"] == 0.0


def _assert_pairs(stage: StageIntent) -> None:
    for left_id, right_id in EXPECTED_PAIRS:
        if left_id.startswith("face"):
            assert left_id not in stage.fixtures and right_id not in stage.fixtures
            continue
        assert (left_id in stage.fixtures) == (right_id in stage.fixtures)
        if left_id not in stage.fixtures:
            continue
        assert stage.fixtures[left_id] == stage.fixtures[right_id]


def _at(show: object, fixture: object, role: ChannelRole, segment: int | None = None) -> int | None:
    profile = show.profile_for(fixture)
    for channel in profile.channels:
        if channel.role is not role:
            continue
        if segment is not None and channel.segment_index != segment:
            continue
        return global_channel(fixture.start_address, channel.local) - 1
    return None
