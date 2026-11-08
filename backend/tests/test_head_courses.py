"""Head courses: a prescribed path, a reachable estimate, and a light gate.

The pose is the program's estimate. It is not a reading from a motor, and it
does not widen the scene's preset spans. Output stays disarmed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from orng_led.audio.auto_show import build_auto_stage
from orng_led.audio.head_motion import (
    HeadRig,
    HeadTravel,
    _mirror_goals,
    _period,
    course_name,
    prescribe,
)
from orng_led.audio.library import BY_ID
from orng_led.config.io import load_show_config
from orng_led.config.models import FixtureKind
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.beam_transform import transform_axis
from orng_led.engine.intents import BeamIntent
from orng_led.engine.renderer import render_stage
from orng_led.show_bench.render import render_frame
from orng_led.show_bench.session import _Encoder
from orng_led.simulator.decode import decode_simulator_view


def _show_beams(show):
    return [fixture for fixture in show.patch.fixtures if fixture.kind is FixtureKind.BEAM]


def _paint(show, rig, beats, look, travel, **kwargs):
    stage = build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "groove",
        0.0,
        0.8,
        look=look,
        beats=beats,
        cycle_beats=1.0,
        placements=show.layout.placements,
        groups=BY_ID[look].groups,
    )
    return preview(show, stage, rig, beats, look, travel, **kwargs)


def preview(show, stage, rig, beats, look, travel, **kwargs):
    from orng_led.audio.head_motion import preview_heads

    return preview_heads(
        show,
        stage,
        beats=beats,
        section="groove",
        look=look,
        beat_period_s=0.5,
        rig=rig,
        travel=travel,
        cycle_beats=1.0,
        **kwargs,
    )


def _travel_for(show) -> HeadTravel:
    spans = {}
    for fixture in _show_beams(show):
        spans[fixture.id] = (0.08, 0.92, 0.24, 0.76)
    return HeadTravel(scene_id="course", revision="course", spans=spans)


def _decoded(show, stage, rig):
    motion = {}
    for fixture in _show_beams(show):
        pose = rig.pose(fixture.id)
        if pose is None:
            continue
        motion[fixture.id] = BeamMotionState(pan=pose[0], tilt=pose[1])
    return decode_simulator_view(show, render_stage(show, stage, motion))


def test_rise_is_dark_then_lit_then_dark_on_the_way_down() -> None:
    show = load_show_config()
    travel = _travel_for(show)
    rig = HeadRig()
    period = _period("heads_only_rise", "groove", 1.0)
    assert period == 8.0
    assert course_name("heads_only_rise") == "rise"
    assert course_name("heads_rise_pulse") == "rise"
    dark = []
    lit = []
    back = []
    previous = None
    beat = 0.0
    while beat <= period + 1e-9:
        stage = _paint(show, rig, beat, "heads_only_rise", travel)
        intent = stage.fixtures["beam_left"]
        assert isinstance(intent, BeamIntent)
        phase = (beat / period) % 1.0
        if phase < 0.2:
            dark.append(intent.dimmer)
        elif 0.3 < phase < 0.48:
            lit.append(intent.dimmer)
        elif 0.55 < phase < 0.74:
            back.append(intent.dimmer)
        pose = rig.pose("beam_left")
        assert pose is not None
        if previous is not None:
            assert abs(pose[0] - previous[0]) <= 0.04 * 0.25 + 1e-6
            assert abs(pose[1] - previous[1]) <= 0.025 * 0.25 + 1e-6
        previous = pose
        beat += 0.25
    assert dark and max(dark) == 0.0
    assert lit and min(lit) > 0.2
    assert back and max(back) == 0.0
    view = _decoded(show, stage, rig)
    for beam in view.beams:
        if beam.id in {"beam_left", "beam_right"}:
            assert 0.0 <= beam.pan <= 1.0
            assert 0.0 <= beam.tilt <= 1.0


def test_courses_are_not_one_sine() -> None:
    shapes = {}
    for name in ("rise", "fan", "arc", "oval", "accent"):
        shapes[name] = tuple(
            (round(prescribe(name, phase).pan, 3), round(prescribe(name, phase).tilt, 3), prescribe(name, phase).lit)
            for phase in (0.0, 0.1, 0.3, 0.45, 0.6, 0.8, 0.99)
        )
    assert len(set(shapes.values())) == 5
    assert shapes["rise"][0][2] is False
    assert shapes["rise"][2][2] is True
    assert shapes["rise"][4][2] is False
    assert shapes["oval"][0] != shapes["fan"][0]
    assert prescribe("accent", 0.1).lit is False
    assert prescribe("accent", 0.4).lit is True


def test_pair_mirrors_about_the_stage_not_each_span_centre() -> None:
    show = load_show_config()
    left = next(fixture for fixture in show.patch.fixtures if fixture.id == "beam_left").model_copy(deep=True)
    right = next(fixture for fixture in show.patch.fixtures if fixture.id == "beam_right").model_copy(deep=True)
    left.spatial = left.spatial.model_copy(
        update={"pan_invert": False, "pan_offset": 0.0, "pan_min": 0.0, "pan_max": 1.0, "home_pan": 0.3}
    )
    right.spatial = right.spatial.model_copy(
        update={
            "pan_invert": True,
            "pan_offset": 0.08,
            "pan_min": 0.1,
            "pan_max": 0.9,
            "home_pan": 0.72,
        }
    )
    travel = HeadTravel(
        scene_id="pair",
        revision="pair",
        spans={
            "beam_left": (0.12, 0.88, 0.2, 0.8),
            "beam_right": (0.2, 0.7, 0.25, 0.75),
        },
    )
    goals = _mirror_goals(
        [left, right],
        beats=2.0,
        section="groove",
        look="heads_only_fan",
        cycle_beats=8.0,
        beat_period_s=0.5,
        travel=travel,
    )
    left_pan, right_pan = goals["beam_left"][0], goals["beam_right"][0]
    assert left_pan is not None and right_pan is not None
    assert abs((left_pan + right_pan) - 1.0) < 1e-3
    physical_left = transform_axis(
        left_pan, offset=0.0, invert=False, axis_min=0.0, axis_max=1.0
    )
    physical_right = transform_axis(
        right_pan, offset=0.08, invert=True, axis_min=0.1, axis_max=0.9
    )
    mid_left = 0.5
    mid_right = (0.1 + 0.9) / 2.0
    assert abs((physical_left - mid_left) + (physical_right - mid_right)) > 0.02
    narrow = HeadTravel(
        scene_id="pair",
        revision="narrow",
        spans={"beam_left": (0.2, 0.8, 0.2, 0.8)},
    )
    held = _mirror_goals(
        [left, right],
        beats=2.0,
        section="groove",
        look="heads_only_fan",
        cycle_beats=8.0,
        beat_period_s=0.5,
        travel=narrow,
    )
    assert held["beam_right"][0] is None
    assert held["beam_left"][0] is not None


def test_blackout_sweep_and_a_new_look_do_not_light_the_return() -> None:
    show = load_show_config()
    travel = _travel_for(show)
    rig = HeadRig()
    _paint(show, rig, 2.5, "heads_only_rise", travel)
    held = rig.pose("beam_left")
    dark = _paint(show, rig, 2.75, "heads_only_rise", travel, blackout=True)
    assert rig.pose("beam_left") == held
    assert dark.fixtures["beam_left"].dimmer == 0.0
    sweep = _paint(show, rig, 3.0, "heads_only_rise", travel, axes_busy=True)
    assert rig.pose("beam_left") == held
    assert sweep.fixtures["beam_left"].dimmer == 0.0
    lost = _paint(show, rig, 4.6, "heads_only_fan", travel, grid_lost=True)
    assert lost.fixtures["beam_left"].dimmer == 0.0
    # The return of the new course stays dark until that course begins again.
    mid = _paint(show, rig, 5.0, "heads_only_fan", travel)
    assert mid.fixtures["beam_left"].dimmer == 0.0


def test_picture_cycle_of_one_beat_is_not_the_motor_cycle() -> None:
    assert _period("heads_only_oval", "groove", 1.0) == _period("heads_only_oval", "groove", None)
    assert _period("heads_only_oval", "groove", 1.0) == 8.0
    assert _period("heads_beat_pass", "groove", 0.5) == 2.0
    assert _period("heads_only_fan", "groove", 4.0) == 4.0
    assert _period("heads_only_oval", "break", None) != 32.0


def test_choreography_video_uses_the_same_estimate() -> None:
    show = load_show_config()
    travel = _travel_for(show)
    placements = {item.fixture_id: item for item in show.layout.placements}
    out = Path(__file__).resolve().parents[2] / "show-bench" / "runs" / "heads"
    out.mkdir(parents=True, exist_ok=True)
    encoder = _Encoder(out / "choreography.mp4", 48000)
    signatures = []
    for look in (
        "heads_only_rise",
        "heads_only_fan",
        "heads_only_arcs",
        "heads_only_oval",
        "heads_beat_pass",
    ):
        rig = HeadRig()
        images = []
        for step in range(1, 25):
            stage = _paint(show, rig, step * 0.5, look, travel)
            image = render_frame(
                _decoded(show, stage, rig),
                placements,
                clock_s=step * 0.25,
                score=look,
                heard="120 BPM",
            )
            images.append(image)
            encoder.write(image)
        signatures.append(images[12].tobytes())
    encoder.close()
    assert (out / "choreography.mp4").stat().st_size > 1000
    assert len(set(signatures)) >= 4
    delta = np.abs(np.frombuffer(signatures[0], dtype=np.uint8).astype(int) - np.frombuffer(signatures[3], dtype=np.uint8).astype(int))
    assert int(delta.sum()) > 100
