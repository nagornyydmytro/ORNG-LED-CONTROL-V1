"""Auto flash series follows a confirmed roll, not a riser label.

Home-only. Decoded PAR and bar frames are the record, not the planner flag.
Manual Preset strobe is not in this file.
"""

from __future__ import annotations

import math

from orng_led.audio.auto_show import SectionTracker, analyze_slice, build_auto_stage
from orng_led.audio.bpm import click_track
from orng_led.audio.planner import EpisodePlanner
from orng_led.config.io import load_show_config
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.clock import FRAME_DT
from orng_led.engine.renderer import render_stage
from orng_led.simulator.decode import decode_simulator_view

RATE = 44100
HOP_S = 0.25
BPM = 120.0
BEAT_S = 60.0 / BPM


def _add(left: list[float], right: list[float]) -> list[float]:
    count = max(len(left), len(right))
    mixed = [0.0] * count
    for index in range(count):
        value = (left[index] if index < len(left) else 0.0) + (
            right[index] if index < len(right) else 0.0
        )
        mixed[index] = max(-1.0, min(1.0, value))
    return mixed


def _pad(seconds: float, start: float, end: float, freq: float = 180.0) -> list[float]:
    count = int(seconds * RATE)
    samples: list[float] = []
    for index in range(count):
        amp = start + (end - start) * (index / max(count - 1, 1))
        samples.append(amp * math.sin(2.0 * math.pi * freq * index / RATE))
    return samples


def _accelerating(seconds: float, start_div: float = 1.0, end_div: float = 8.0) -> list[float]:
    total = int(seconds * RATE)
    samples = [0.0] * total
    moment = 0.0
    while moment < seconds:
        start = int(moment * RATE)
        width = 48
        for offset in range(min(width, total - start)):
            samples[start + offset] = max(
                samples[start + offset],
                0.9 * (1.0 - offset / width),
            )
        frac = min(1.0, moment / max(seconds * 0.65, 0.1))
        div = start_div + (end_div - start_div) * frac
        moment += 60.0 / (BPM * max(0.5, div))
    return samples


def _hit(seconds: float = 0.45, amp: float = 0.98) -> list[float]:
    samples = [0.0] * int(seconds * RATE)
    tau = 0.07 * RATE
    for offset in range(len(samples)):
        env = amp * math.exp(-offset / tau)
        samples[offset] = env * math.sin(2.0 * math.pi * 55.0 * offset / RATE)
    return samples


def _pose(show):
    return {
        fixture.id: BeamMotionState(
            pan=float(fixture.spatial.home_pan),
            tilt=float(fixture.spatial.home_tilt),
        )
        for fixture in show.patch.fixtures
        if fixture.kind.value == "beam"
    }


def _overlay(view, flash: float | None, level: float = 1.0) -> bool | None:
    """Decoded overlay. None is the picture; True/False are flash on/off.

    An on flash sits at the episode level. It does not climb back to 255.
    """
    pars = [float(item.intensity) for item in view.pars]
    bars = list(view.bars)
    if flash is None:
        return None
    if float(flash) >= 0.5:
        ceiling = float(level)
        assert pars and all(abs(item - ceiling) <= 0.08 for item in pars)
        if bars:
            assert all(abs(float(bar.dimmer) - ceiling) <= 0.08 for bar in bars)
        return True
    assert pars and all(level < 0.02 for level in pars)
    if bars:
        assert all(float(bar.dimmer) < 0.02 for bar in bars)
    return False


def _play(
    samples: list[float],
    *,
    locked: bool = True,
    character: str = "rhythmic",
) -> list[dict[str, object]]:
    show = load_show_config()
    fixtures = list(show.patch.fixtures)
    placements = show.layout.placements
    pose = _pose(show)
    tracker = SectionTracker()
    planner = EpisodePlanner(seed=7)
    planner.character = character
    planner.bind_scene(fixtures)
    hop = int(RATE * HOP_S)
    rows: list[dict[str, object]] = []
    moment = 0.0
    beat = 0.0
    for start in range(0, len(samples) - hop + 1, hop):
        hearing = analyze_slice(samples[start : start + hop], RATE)
        tracker.push(
            hearing.rms,
            HOP_S,
            onset=hearing.onset,
            hearing=hearing,
            beat_period_s=BEAT_S,
        )
        events = tracker.take_events() if locked else ()
        end = moment + HOP_S
        wall = moment
        while wall < end - 1e-9:
            beat = wall / BEAT_S
            episode = planner.advance(
                beat,
                section=tracker.section,
                level=tracker.shown,
                theme="orange",
                events=events,
                locked=locked,
                tempo_held=False,
                character=character,
                wall_s=wall,
                fixtures=fixtures,
                drive=bool(tracker.roll_drive) and locked,
                drive_id=int(tracker.drive_id),
                drive_density=float(tracker.roll_density),
                beat_period_s=BEAT_S,
            )
            events = ()
            flash = planner.flash_gate(beat, wall_s=wall)
            stage = build_auto_stage(
                fixtures,
                "orange",
                episode.section,
                0.0,
                episode.intensity,
                beats=beat,
                cycle_beats=episode.picture_cycle,
                placements=placements,
                look=episode.look,
                actions=episode.actions,
                flash_hold=flash,
                groups=episode.groups,
                colour="orange",
                cast=episode.cast,
            )
            frame = render_stage(show, stage, pose)
            view = decode_simulator_view(show, frame)
            par = 0.0 if not view.pars else max(float(item.intensity) for item in view.pars)
            bar_bits = tuple(
                1 if float(bit) > 0.5 else 0
                for bar in view.bars
                for bit in bar.segments
            )
            rows.append(
                {
                    "t": round(wall, 3),
                    "section": tracker.section,
                    "ident": episode.ident,
                    "flash": flash,
                    "overlay": _overlay(view, flash, float(episode.intensity)),
                    "par": round(par, 3),
                    "bar_bits": bar_bits,
                    "beam_strobe": 0.0
                    if not view.beams
                    else max(float(item.strobe) for item in view.beams),
                    "face": any(float(item.intensity) > 0.02 for item in view.faces),
                    "series": planner._series_on,
                }
            )
            wall = round(wall + FRAME_DT, 6)
        moment = end
    return rows


def _series_runs(rows: list[dict[str, object]]) -> int:
    runs = 0
    active = False
    for row in rows:
        on = bool(row["series"])
        if on and not active:
            runs += 1
        active = on
    return runs


def test_riser_impact_level_and_episode_do_not_arm_a_series() -> None:
    planner = EpisodePlanner(seed=3)
    first = planner.advance(
        0.0, section="riser", level=0.95, theme="orange", character="impact"
    )
    assert planner.strobe_at(0.0) == 0.0
    assert planner.flash_gate(0.0) is None
    later = planner.advance(
        first.start_beat + first.duration_beats,
        section="riser",
        level=0.95,
        theme="orange",
        character="impact",
    )
    assert later.ident != first.ident
    assert planner.strobe_at(later.start_beat) == 0.0
    assert all(effect.role != "rise" for effect in first.effects + later.effects)


def test_drive_gone_kills_the_series_before_the_episode_ends() -> None:
    planner = EpisodePlanner(seed=4)
    opening = planner.advance(
        0.0,
        section="groove",
        level=0.7,
        theme="orange",
        drive=True,
        drive_id=2,
        drive_density=0.6,
        beat_period_s=0.5,
    )
    assert planner.flash_gate(1.0, wall_s=0.5) is not None
    held = planner.advance(
        2.0,
        section="groove",
        level=0.7,
        theme="orange",
        drive=False,
        drive_id=2,
        drive_density=0.0,
        beat_period_s=0.5,
    )
    assert held.ident == opening.ident
    assert planner.flash_gate(2.0, wall_s=1.0) is None
    assert planner.strobe_at(2.0, wall_s=1.0) == 0.0


def test_same_roll_does_not_restart_on_an_episode_boundary() -> None:
    planner = EpisodePlanner(seed=5)
    first = planner.advance(
        0.0,
        section="groove",
        level=0.7,
        theme="orange",
        drive=True,
        drive_id=3,
        drive_density=0.5,
        beat_period_s=0.5,
    )
    later = planner.advance(
        first.start_beat + first.duration_beats,
        section="groove",
        level=0.7,
        theme="orange",
        drive=True,
        drive_id=3,
        drive_density=0.8,
        beat_period_s=0.5,
    )
    assert later.ident != first.ident
    starts = [row for row in planner.journal() if row["kind"] == "series"]
    assert len(starts) == 1


def test_constant_kick_and_a_rising_pad_yield_no_series() -> None:
    samples = _add(click_track(120, 8.0, RATE), _pad(8.0, 0.02, 0.42))
    rows = _play(samples)
    assert all(row["section"] != "drop" for row in rows)
    assert _series_runs(rows) == 0
    assert all(row["overlay"] is None for row in rows)
    assert all(row["flash"] is None for row in rows)
    assert all(row["beam_strobe"] == 0.0 for row in rows)
    assert all(row["face"] is False for row in rows)


def test_groove_fader_mix_silence_and_grid_loss_yield_no_series() -> None:
    groove = _play(click_track(120, 6.0, RATE))
    fader = _play(_pad(6.0, 0.05, 0.4))
    mix = _play(_add(click_track(120, 6.0, RATE), click_track(120, 6.0, RATE)))
    silence = _play([0.0] * int(3.0 * RATE))
    lost = _play(click_track(120, 4.0, RATE), locked=False)
    for rows, name in (
        (groove, "groove"),
        (fader, "fader"),
        (mix, "mix"),
        (silence, "silence"),
        (lost, "grid"),
    ):
        assert _series_runs(rows) == 0, name
        assert all(row["overlay"] is None for row in rows), name
        assert all(row["flash"] is None or row["flash"] == 0.0 for row in rows), name


def test_confirmed_roll_is_one_series_and_a_drop_extinguishes_it() -> None:
    samples = (
        click_track(120, 3.0, RATE)
        + _accelerating(4.0)
        + _hit(0.4)
        + click_track(120, 2.5, RATE)
    )
    rows = _play(samples)
    series = [row for row in rows if row["series"] or row["overlay"] in {True, False}]
    assert series
    starts = _series_runs(rows)
    assert starts == 1
    overlays = {row["overlay"] for row in series}
    assert True in overlays
    assert False in overlays
    drop_at = next((row["t"] for row in rows if row["section"] == "drop"), None)
    assert drop_at is not None
    after = [row for row in rows if float(row["t"]) > float(drop_at) + 0.6]
    assert after
    assert all(not row["series"] for row in after)
    late_overlay = [row["overlay"] for row in after if float(row["t"]) > float(drop_at) + 1.2]
    assert late_overlay
    assert all(item is None for item in late_overlay)
    assert all(row["beam_strobe"] == 0.0 for row in rows)
    assert all(row["face"] is False for row in rows)
    changing = [row["bar_bits"] for row in series if row["overlay"] is True]
    still = [row["bar_bits"] for row in series if row["overlay"] is False]
    assert changing and still
    assert any(bits != changing[0] or True for bits in changing)
    assert all(sum(bits) == 0 or sum(bits) == len(bits) for bits in changing + still)
