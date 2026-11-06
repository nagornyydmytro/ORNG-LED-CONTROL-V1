"""Auto section ear on an audio stream. No manual section assignment.

Home-only. Uses constructed club figures and cached CC0 clips from the
previous music-fragment task. This is not a USB venue take and not a second
BPM estimator.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from orng_led.audio.auto_show import SectionTracker, StructuralEvent, analyze_slice
from orng_led.audio.bpm import click_track
from tests.music_clips import (
    DANCE_120,
    HOUSE_128,
    RATE,
    ensure_clip,
    loop_to,
    read_mono,
    scale,
)

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


def _pad(
    seconds: float, start: float, end: float | None = None, freq: float = 180.0
) -> list[float]:
    count = int(seconds * RATE)
    stop = start if end is None else end
    samples: list[float] = []
    for index in range(count):
        amp = start + (stop - start) * (index / max(count - 1, 1))
        samples.append(amp * math.sin(2.0 * math.pi * freq * index / RATE))
    return samples


def _clicks(
    seconds: float,
    *,
    division: float = 1.0,
    amp: float = 0.9,
    bpm: float = BPM,
) -> list[float]:
    total = int(seconds * RATE)
    samples = [0.0] * total
    step = max(1, int(RATE * 60.0 / (bpm * division)))
    width = 90 if division <= 2.0 else 48
    for start in range(0, total, step):
        for offset in range(min(width, total - start)):
            samples[start + offset] = amp * (1.0 - offset / width)
    return samples


def _accelerating(
    seconds: float,
    *,
    start_div: float = 1.0,
    end_div: float = 4.0,
    bpm: float = BPM,
) -> list[float]:
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
        moment += 60.0 / (bpm * max(0.5, div))
    return samples


def _hit(seconds: float = 0.45, amp: float = 0.98) -> list[float]:
    samples = [0.0] * int(seconds * RATE)
    tau = 0.07 * RATE
    for offset in range(len(samples)):
        env = amp * math.exp(-offset / tau)
        samples[offset] = env * math.sin(2.0 * math.pi * 55.0 * offset / RATE)
    return samples


def _plant_hat(samples: list[float], time_s: float, amp: float = 0.42) -> None:
    start = int(time_s * RATE)
    if start < 0 or start >= len(samples):
        return
    for offset in range(min(28, len(samples) - start)):
        env = amp * (1.0 - offset / 28.0)
        samples[start + offset] += env * math.sin(2.0 * math.pi * 8000.0 * offset / RATE)


def _crossfade(left: list[float], right: list[float], fade_s: float) -> list[float]:
    fade_n = max(1, int(fade_s * RATE))
    head = left[:-fade_n] if len(left) > fade_n else []
    tail = right[fade_n:] if len(right) > fade_n else []
    mixed: list[float] = list(head)
    for index in range(fade_n):
        frac = index / max(fade_n - 1, 1)
        a = left[-fade_n + index] if len(left) >= fade_n else 0.0
        b = right[index] if index < len(right) else 0.0
        mixed.append(max(-1.0, min(1.0, (1.0 - frac) * a + frac * b)))
    mixed.extend(tail)
    return mixed


def _clip(spec) -> list[float]:
    return read_mono(ensure_clip(spec))


StreamRow = tuple[float, str, float]
StreamLog = list[tuple[float, str, str]]


def _stream(
    samples: list[float],
) -> tuple[list[StreamRow], tuple[StructuralEvent, ...], StreamLog]:
    tracker = SectionTracker()
    hop = int(RATE * HOP_S)
    rows: list[tuple[float, str, float]] = []
    events: list[StructuralEvent] = []
    moment = 0.0
    for start in range(0, len(samples) - hop + 1, hop):
        hearing = analyze_slice(samples[start : start + hop], RATE)
        tracker.push(
            hearing.rms,
            HOP_S,
            onset=hearing.onset,
            hearing=hearing,
            beat_period_s=BEAT_S,
        )
        moment += HOP_S
        rows.append((round(moment, 2), tracker.section, round(tracker.level, 3)))
        events.extend(tracker.take_events())
    return rows, tuple(events), list(tracker.journal)


def _names(rows: Sequence[tuple[float, str, float]]) -> list[str]:
    names: list[str] = []
    for _, section, _ in rows:
        if not names or names[-1] != section:
            names.append(section)
    return names


def _span(rows: Sequence[tuple[float, str, float]], start: float, end: float) -> set[str]:
    return {section for moment, section, _ in rows if start <= moment <= end}


def _timeline(journal: Sequence[tuple[float, str, str]], events: Sequence[StructuralEvent]) -> str:
    parts = [f"{when:.1f}s {kind}:{detail}" for when, kind, detail in journal]
    false_drop = [event for event in events if event.kind != "drop"]
    extra = [f"{event.at_s:.1f}s event:{event.kind}" for event in events]
    guessed = [f"{event.at_s:.1f}s false:{event.kind}" for event in false_drop]
    return " | ".join(parts + extra + guessed) or "(no decisions)"


def test_section_stream_splits_level_activity_rise_and_texture() -> None:
    dance = loop_to(_clip(DANCE_120), 8.0)
    house = loop_to(_clip(HOUSE_128), 8.0)
    cases: list[tuple[str, list[float]]] = [
        ("even-groove", click_track(BPM, 8.0, RATE)),
        ("dance-clip", dance),
        (
            "fader",
            scale(click_track(BPM, 3.0, RATE), 0.22)
            + scale(click_track(BPM, 3.0, RATE), 1.0)
            + scale(click_track(BPM, 3.0, RATE), 0.22),
        ),
        (
            "quiet-same",
            scale(click_track(BPM, 4.0, RATE), 0.9)
            + scale(click_track(BPM, 4.0, RATE), 0.12),
        ),
        (
            "kick-dropout",
            _add(click_track(BPM, 4.0, RATE), _pad(4.0, 0.05))
            + _pad(3.0, 0.05)
            + _add(click_track(BPM, 3.5, RATE), _pad(3.5, 0.05)),
        ),
        (
            "roll-hit",
            click_track(BPM, 3.0, RATE) + _accelerating(3.5) + _hit() + click_track(BPM, 2.0, RATE),
        ),
        (
            "roll-no-hit",
            click_track(BPM, 3.0, RATE) + _accelerating(4.0) + click_track(BPM, 3.0, RATE),
        ),
        ("mix-120", _crossfade(dance, house, 4.0)),
    ]
    hat = click_track(BPM, 6.0, RATE)
    _plant_hat(hat, 3.13)
    cases.append(("one-hat", hat))
    hit = click_track(BPM, 6.0, RATE)
    planted = _hit(0.35, amp=0.99)
    start = int(3.0 * RATE)
    for index, value in enumerate(planted):
        if start + index < len(hit):
            hit[start + index] = max(-1.0, min(1.0, hit[start + index] + value))
    cases.append(("one-hit", hit))
    pad_kick = _add(click_track(BPM, 6.0, RATE), _pad(6.0, 0.02, 0.36))
    cases.append(("pad-swell", pad_kick))

    lines: list[str] = []
    by_name: dict[str, tuple[list[StreamRow], tuple[StructuralEvent, ...], StreamLog]] = {}
    for name, samples in cases:
        rows, events, journal = _stream(samples)
        by_name[name] = (rows, events, journal)
        lines.append(f"{name}: {_names(rows)} :: {_timeline(journal, events)}")

    even_rows, even_events, _ = by_name["even-groove"]
    assert _names(even_rows) == ["groove"]
    assert even_events == ()

    dance_rows, dance_events, _ = by_name["dance-clip"]
    assert "drop" not in _names(dance_rows)
    assert dance_rows[-1][1] == "groove"
    assert dance_events == ()

    fader_rows, fader_events, fader_log = by_name["fader"]
    assert "drop" not in _names(fader_rows)
    assert "riser" not in _names(fader_rows)
    assert fader_events == ()
    assert fader_rows[-1][1] == "groove"
    assert any(kind == "gain" or detail == "level" for _when, kind, detail in fader_log) or all(
        section == "groove" for _, section, _ in fader_rows
    )

    quiet_rows, quiet_events, _ = by_name["quiet-same"]
    assert "drop" not in _names(quiet_rows)
    assert quiet_rows[-1][1] == "groove"
    assert quiet_events == ()
    early = [level for moment, _, level in quiet_rows if moment <= 3.5]
    late = [level for moment, _, level in quiet_rows if moment >= 6.5]
    assert late and early
    assert sum(late) / len(late) < sum(early) / len(early) * 0.9

    drop_rows, drop_events, _ = by_name["kick-dropout"]
    assert "break" in _span(drop_rows, 4.5, 6.8)
    assert drop_rows[-1][1] == "groove"
    assert all(event.kind != "drop" for event in drop_events)

    rise_hit_rows, rise_hit_events, _ = by_name["roll-hit"]
    assert "riser" in _names(rise_hit_rows)
    assert "drop" in _names(rise_hit_rows)
    assert rise_hit_rows[-1][1] != "drop"
    assert len(rise_hit_events) == 1
    assert rise_hit_events[0].kind == "drop"

    rise_rows, rise_events, _ = by_name["roll-no-hit"]
    assert "riser" in _names(rise_rows)
    assert "drop" not in _names(rise_rows)
    assert rise_rows[-1][1] == "groove"
    assert rise_events == ()

    mix_rows, mix_events, mix_log = by_name["mix-120"]
    assert mix_rows[-1][1] == "groove"
    assert "drop" not in _names(mix_rows)
    assert mix_events == ()
    assert all(kind != "drop" and detail != "track" for _when, kind, detail in mix_log)

    hat_rows, hat_events, _ = by_name["one-hat"]
    assert _names(hat_rows) == ["groove"]
    assert hat_events == ()

    hit_rows, hit_events, hit_log = by_name["one-hit"]
    assert "drop" not in _names(hit_rows)
    assert hit_events == ()
    assert hit_rows[-1][1] == "groove"
    rejected_hit = any(detail == "hit" for _when, _kind, detail in hit_log)
    assert rejected_hit or "riser" not in _names(hit_rows)

    pad_rows, pad_events, pad_log = by_name["pad-swell"]
    assert "riser" not in _names(pad_rows)
    assert "drop" not in _names(pad_rows)
    assert pad_events == ()
    assert any(detail == "level" for _when, _kind, detail in pad_log) or pad_rows[-1][1] == "groove"

    print("section timeline")
    for line in lines:
        print(line)


def test_look_path_does_not_analyse_a_slice() -> None:
    tracker = SectionTracker()
    hearing = analyze_slice(click_track(BPM, 0.5, RATE), RATE)
    tracker.push(hearing.rms, 0.25, onset=hearing.onset, hearing=hearing, beat_period_s=BEAT_S)
    pattern = tracker.pattern
    section = tracker.section
    tracker.observe(1.0, 0.2, 0.1)
    tracker.observe(1.05, 0.3, 0.12)
    assert tracker.section == section
    assert tracker.pattern == pattern
    assert tracker.journal == []
