"""Desk preview uses the performer model. A catalog clip is not that plan."""

from orng_led.audio.head_motion import motion_loop_beats
from orng_led.audio.preview import phase_frame_count, preview_clip
from orng_led.config.io import load_show_config


def test_head_preview_uses_the_mechanical_period_not_the_picture_cycle() -> None:
    show = load_show_config()
    clip = preview_clip(show, "heads_only_oval", cycle_beats=1, bpm=120, frames=0)
    loop = float(clip["loop_beats"])
    assert loop == motion_loop_beats("heads_only_oval", 1)
    assert loop in {2.0, 4.0, 8.0}
    assert loop != 1.0
    assert loop != 32.0
    assert len(clip["frames"]) == phase_frame_count(1)


def test_oval_preview_seam_stays_on_the_motion_loop() -> None:
    show = load_show_config()
    clip = preview_clip(show, "heads_only_oval", cycle_beats=8, bpm=120, frames=0)
    frames = clip["frames"]
    assert float(clip["loop_beats"]) == 8.0
    pans = [
        float(next(beam for beam in frame["beams"] if beam["id"] == "beam_left")["pan"])
        for frame in frames
    ]
    steps = [abs(pans[index + 1] - pans[index]) for index in range(len(pans) - 1)]
    assert abs(pans[0] - pans[-1]) <= max(steps) + (1.0 / 255.0)
    left = next(beam for beam in frames[len(frames) // 3]["beams"] if beam["id"] == "beam_left")
    right = next(beam for beam in frames[len(frames) // 3]["beams"] if beam["id"] == "beam_right")
    assert abs((float(left["pan"]) + float(right["pan"])) - 1.0) < 0.05
