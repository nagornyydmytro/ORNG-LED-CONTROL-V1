"""Each head path keeps its own memory. Preview does not move a live rig."""

from __future__ import annotations

from orng_led.audio.auto_show import build_auto_stage
from orng_led.audio.head_motion import HeadRig, preview_heads, target
from orng_led.audio.preview import preview_clip
from orng_led.audio.service import BpmService
from orng_led.config.io import load_show_config
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.intents import BeamIntent
from orng_led.engine.renderer import render_stage


def _stage(show, look: str, beats: float):
    return build_auto_stage(
        list(show.patch.fixtures),
        "orange",
        "groove",
        0.0,
        0.8,
        look=look,
        beats=beats,
        cycle_beats=8.0,
        placements=show.layout.placements,
        groups=("beam",),
    )


def _paint(show, rig: HeadRig, beats: float, look: str = "heads_only_fan"):
    return preview_heads(
        show,
        _stage(show, look, beats),
        beats=beats,
        section="groove",
        look=look,
        rig=rig,
    )


def _frames(show, rig: HeadRig, beats: list[float]) -> list[list[int]]:
    frames = []
    for beat in beats:
        stage = _paint(show, rig, beat)
        motion: dict[str, BeamMotionState] = {}
        for fixture in show.patch.fixtures:
            pose = rig.pose(fixture.id)
            if pose is None:
                continue
            motion[fixture.id] = BeamMotionState(pan=pose[0], tilt=pose[1])
        frames.append(render_stage(show, stage, motion))
    return frames


def test_preview_clip_does_not_move_a_running_rig() -> None:
    show = load_show_config()
    beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
    live = HeadRig()
    control = HeadRig()
    before = _frames(show, live, beats[:-1])
    control_before = _frames(show, control, beats[:-1])
    assert before == control_before
    preview_clip(show, "heads_only_oval", cycle_beats=8, frames=12)
    preview_clip(show, "heads_only_fan", cycle_beats=8, frames=4)
    after = _frames(show, live, beats[-1:])
    control_after = _frames(show, control, beats[-1:])
    assert after == control_after
    left = _paint(show, live, 3.0).fixtures["beam_left"]
    other = _paint(show, control, 3.0).fixtures["beam_left"]
    assert isinstance(left, BeamIntent) and isinstance(other, BeamIntent)
    assert left.pan == other.pan
    assert left.tilt == other.tilt


def test_scene_blackout_and_sweep_drop_only_that_rig(tmp_path) -> None:
    show = load_show_config()
    service = BpmService(tmp_path, "orng-bar")
    other = HeadRig()
    _paint(show, service.heads, 1.0)
    _paint(show, other, 1.0)
    assert service.heads.pose("beam_left") == other.pose("beam_left")
    service.look(list(show.patch.fixtures), show.layout.placements, axes_busy=True)
    assert service.heads.pose("beam_left") is None
    assert other.pose("beam_left") is not None
    _paint(show, service.heads, 1.5)
    service.look(list(show.patch.fixtures), show.layout.placements, blackout=True)
    assert service.heads.pose("beam_left") is None
    held = other.pose("beam_left")
    service.set_scene("orng-bar")
    assert service.heads.pose("beam_left") is None
    assert other.pose("beam_left") == held


def test_skipped_beat_stays_inside_the_speed_limit() -> None:
    show = load_show_config()
    rig = HeadRig()
    _paint(show, rig, 0.0)
    home = rig.pose("beam_right")
    assert home is not None
    goal = target(
        4.0,
        1.0,
        "groove",
        look="heads_only_fan",
        pan_span=(0.08, 0.92),
        tilt_span=(0.24, 0.64),
    )
    _paint(show, rig, 4.0)
    moved = rig.pose("beam_right")
    assert moved is not None
    assert abs(moved[0] - home[0]) <= 0.04 * 4.0 + 1e-9
    assert abs(moved[1] - home[1]) <= 0.025 * 4.0 + 1e-9
    assert goal[0] is None or abs(moved[0] - home[0]) <= abs(float(goal[0]) - home[0]) + 1e-9
