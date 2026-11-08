"""Original synthesized program for the home bench.

This is not a captured phonogram and not a commercial recording.
The markup describes the waveform the generator wrote. It is not a tempo.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

GENERATOR = "orng-show-bench-synthesis-1"
RATE = 44100
ORIGIN = "original-synthesis"


def _env(n: int, rate: int, seconds: float) -> np.ndarray:
    length = max(1, int(seconds * rate))
    length = min(length, n)
    curve = np.linspace(1.0, 0.0, length, dtype=np.float64)
    out = np.zeros(n, dtype=np.float64)
    out[:length] = curve * curve
    return out


def _kick(rate: int) -> np.ndarray:
    n = int(0.18 * rate)
    t = np.arange(n, dtype=np.float64) / rate
    body = np.sin(2.0 * np.pi * (90.0 - 55.0 * t / 0.18) * t)
    return body * _env(n, rate, 0.18) * 0.9


def _hat(rate: int, rng: np.random.Generator, *, seconds: float = 0.04) -> np.ndarray:
    n = int(seconds * rate)
    noise = rng.normal(0.0, 1.0, n)
    noise = np.diff(noise, prepend=noise[0])
    return noise * _env(n, rate, seconds) * 0.22


def _tone(rate: int, freq: float, seconds: float, *, amp: float = 0.2) -> np.ndarray:
    n = int(seconds * rate)
    t = np.arange(n, dtype=np.float64) / rate
    wave = np.sin(2.0 * np.pi * freq * t) + 0.35 * np.sin(2.0 * np.pi * freq * 2.0 * t)
    attack = min(n, int(0.03 * rate))
    release = min(n, int(0.08 * rate))
    env = np.ones(n, dtype=np.float64)
    env[:attack] = np.linspace(0.0, 1.0, attack)
    env[-release:] *= np.linspace(1.0, 0.0, release)
    return wave * env * amp


def _place(buf: np.ndarray, start: int, clip: np.ndarray, amp: float = 1.0) -> None:
    if start >= buf.size or amp == 0.0:
        return
    end = min(buf.size, start + clip.size)
    buf[start:end] += clip[: end - start] * amp


def _beats(duration: float, bpm: float) -> range:
    return range(int(duration * bpm / 60.0))


def even_house(rate: int = RATE) -> np.ndarray:
    """Four on the floor, offbeat hat, root bass. Even."""
    duration = 26.0
    bpm = 124.0
    n = int(duration * rate)
    buf = np.zeros(n, dtype=np.float64)
    kick = _kick(rate)
    rng = np.random.default_rng(124)
    hat = _hat(rate, rng)
    step = 60.0 / bpm
    bass_notes = (55.0, 55.0, 73.4, 55.0)
    for index in _beats(duration, bpm):
        at = int(index * step * rate)
        _place(buf, at, kick, 0.85)
        _place(buf, at + int(step * 0.5 * rate), hat, 0.7)
        if index % 2 == 0:
            _place(buf, at, _tone(rate, bass_notes[(index // 2) % 4], step * 1.6, amp=0.28))
    return buf


def dense_techno(rate: int = RATE) -> tuple[np.ndarray, dict[str, float]]:
    """Busy hats and a rumble, then a real volume dip against the same groove."""
    duration = 26.0
    bpm = 138.0
    n = int(duration * rate)
    buf = np.zeros(n, dtype=np.float64)
    kick = _kick(rate)
    rng = np.random.default_rng(138)
    hat = _hat(rate, rng, seconds=0.03)
    tick = _hat(rate, rng, seconds=0.015)
    step = 60.0 / bpm
    for index in _beats(duration, bpm):
        at = int(index * step * rate)
        _place(buf, at, kick, 1.0)
        _place(buf, at + int(step * 0.25 * rate), tick, 0.45)
        _place(buf, at + int(step * 0.5 * rate), hat, 0.8)
        _place(buf, at + int(step * 0.75 * rate), tick, 0.35)
        _place(buf, at, _tone(rate, 41.2, step * 0.9, amp=0.22))
    dip_at = 8.0
    dip_for = 4.0
    a = int(dip_at * rate)
    b = int((dip_at + dip_for) * rate)
    ramp = np.linspace(1.0, 0.12, int(0.4 * rate))
    back = np.linspace(0.12, 1.0, int(0.6 * rate))
    buf[a : a + ramp.size] *= ramp
    buf[a + ramp.size : b - back.size] *= 0.12
    buf[b - back.size : b] *= back
    return buf, {"dip_start_s": dip_at, "dip_end_s": dip_at + dip_for}


def broken_beat(rate: int = RATE) -> np.ndarray:
    """Fast grid with kicks that skip the straight pulse."""
    duration = 22.0
    bpm = 174.0
    n = int(duration * rate)
    buf = np.zeros(n, dtype=np.float64)
    kick = _kick(rate)
    rng = np.random.default_rng(174)
    hat = _hat(rate, rng, seconds=0.02)
    step = 60.0 / bpm
    # 3+3+2 sixteenths inside a bar of 16, at a fast tempo.
    pattern = {0, 3, 6, 8, 11, 14}
    for index in _beats(duration, bpm):
        at = int(index * step * rate)
        if index % 16 in pattern:
            _place(buf, at, kick, 0.75)
        if index % 2 == 1:
            _place(buf, at, hat, 0.55)
        if index % 8 == 4:
            _place(buf, at, _tone(rate, 49.0, step * 1.2, amp=0.16))
    return buf


def slow_syncopated(rate: int = RATE) -> np.ndarray:
    """Slow pulse. The second hit sits off the beat."""
    duration = 22.0
    bpm = 78.0
    n = int(duration * rate)
    buf = np.zeros(n, dtype=np.float64)
    kick = _kick(rate)
    rng = np.random.default_rng(78)
    hat = _hat(rate, rng, seconds=0.05)
    step = 60.0 / bpm
    for index in _beats(duration, bpm):
        at = int(index * step * rate)
        if index % 4 == 0:
            _place(buf, at, kick, 0.7)
        if index % 4 == 2:
            late = int(step * 0.22 * rate)
            _place(buf, at + late, kick, 0.55)
        if index % 2 == 1:
            _place(buf, at, hat, 0.25)
        if index % 4 == 0:
            _place(buf, at, _tone(rate, 65.4 if (index // 4) % 2 == 0 else 49.0, step * 3.2, amp=0.2))
    return buf


def melody_without_a_beat(rate: int = RATE) -> np.ndarray:
    """Chords and a line. No kick and no hat."""
    duration = 18.0
    n = int(duration * rate)
    t = np.arange(n, dtype=np.float64) / rate
    chords = ((196.0, 246.9, 293.7), (174.6, 220.0, 261.6), (220.0, 277.2, 329.6))
    buf = np.zeros(n, dtype=np.float64)
    for index, chord in enumerate(chords):
        start = int(index * duration / 3.0 * rate)
        for freq in chord:
            tone = _tone(rate, freq, duration / 3.0 + 0.2, amp=0.12)
            _place(buf, start, tone)
    line = (392.0, 440.0, 349.2, 329.6, 392.0, 293.7)
    for index, freq in enumerate(line):
        start = int(index * (duration / len(line)) * rate)
        vibrato = 1.0 + 0.015 * np.sin(2.0 * np.pi * 4.5 * t[start : start + int(2.4 * rate)])
        tone = _tone(rate, freq, 2.4, amp=0.16)
        take = min(tone.size, vibrato.size)
        _place(buf, start, tone[:take] * vibrato[:take])
    return buf


def buildup_and_drop(rate: int = RATE) -> tuple[np.ndarray, dict[str, float]]:
    """A straight section, a rising roll, then a drop on the same tempo."""
    groove = 10.0
    build = 10.0
    drop = 10.0
    duration = groove + build + drop
    bpm = 128.0
    n = int(duration * rate)
    buf = np.zeros(n, dtype=np.float64)
    kick = _kick(rate)
    rng = np.random.default_rng(128)
    hat = _hat(rate, rng)
    step = 60.0 / bpm
    for index in _beats(groove, bpm):
        at = int(index * step * rate)
        _place(buf, at, kick, 0.55)
        _place(buf, at + int(step * 0.5 * rate), hat, 0.35)
    build_start = int(groove * rate)
    roll_n = int(build * rate)
    rise = np.linspace(180.0, 2400.0, roll_n)
    noise = rng.normal(0.0, 1.0, roll_n)
    noise = np.diff(noise, prepend=noise[0])
    buf[build_start : build_start + roll_n] += noise * np.linspace(0.02, 0.35, roll_n)
    phase = np.cumsum(rise) / rate
    buf[build_start : build_start + roll_n] += 0.08 * np.sin(2.0 * np.pi * phase)
    # Accelerating snare-like ticks through the build.
    gap = 0.5
    cursor = groove
    while cursor < groove + build:
        at = int(cursor * rate)
        _place(buf, at, hat, 0.4 + 0.5 * ((cursor - groove) / build))
        gap = max(0.06, gap * 0.92)
        cursor += gap
    drop_at = groove + build
    for index in _beats(drop, bpm):
        at = int((drop_at + index * step) * rate)
        _place(buf, at, kick, 1.0)
        _place(buf, at + int(step * 0.5 * rate), hat, 0.9)
        _place(buf, at, _tone(rate, 55.0, step, amp=0.3))
    return buf, {
        "build_start_s": groove,
        "drop_start_s": drop_at,
        "drop_end_s": duration,
    }


def _xfade(left: np.ndarray, right: np.ndarray, rate: int, seconds: float = 0.6) -> np.ndarray:
    n = int(seconds * rate)
    n = min(n, left.size, right.size)
    if n <= 0:
        return np.concatenate((left, right))
    fade = np.linspace(0.0, 1.0, n)
    joined = np.concatenate((left[:-n], left[-n:] * (1.0 - fade) + right[:n] * fade, right[n:]))
    return joined


def _silence(rate: int, seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * rate), dtype=np.float64)


def _piece(ident: str, title: str, start: float, end: float) -> dict[str, object]:
    return {
        "kind": "piece",
        "id": ident,
        "title": title,
        "start_s": round(start, 3),
        "end_s": round(end, 3),
        "origin": ORIGIN,
    }


def build_program(rate: int = RATE) -> tuple[np.ndarray, dict[str, object]]:
    """One program: six pieces, crossfades, a volume dip, and a silence."""
    house = even_house(rate)
    techno, dip = dense_techno(rate)
    broken = broken_beat(rate)
    slow = slow_syncopated(rate)
    melody = melody_without_a_beat(rate)
    arc, arc_marks = buildup_and_drop(rate)
    pause_s = 2.5
    pieces: list[tuple[str, str, np.ndarray]] = [
        ("even_house", "ровный house", house),
        ("dense_techno", "плотный techno", techno),
        ("broken_beat", "broken beat, быстрый ритм", broken),
        ("slow_syncopated", "медленный синкопированный", slow),
        ("melody", "мелодия без явного бита", melody),
        ("buildup_drop", "build-up и drop", arc),
    ]
    timeline: list[dict[str, object]] = []
    program = pieces[0][2]
    start = 0.0
    timeline.append(_piece(pieces[0][0], pieces[0][1], start, program.size / rate))
    for ident, title, clip in pieces[1:]:
        if ident == "melody":
            silence_at = program.size / rate
            program = np.concatenate((program, _silence(rate, pause_s), clip))
            start = silence_at + pause_s
            timeline.append(
                {
                    "kind": "silence",
                    "start_s": round(silence_at, 3),
                    "end_s": round(start, 3),
                    "origin": ORIGIN,
                }
            )
        else:
            before = program.size
            program = _xfade(program, clip, rate)
            overlap = (before + clip.size) - program.size
            start = (before - overlap) / rate
            timeline.append(
                {
                    "kind": "crossfade",
                    "start_s": round(start, 3),
                    "end_s": round((before) / rate, 3),
                    "into": ident,
                    "origin": ORIGIN,
                }
            )
        end = program.size / rate
        timeline.append(_piece(ident, title, start, end))
        if ident == "dense_techno":
            timeline.append(
                {
                    "kind": "volume_dip",
                    "start_s": round(start + float(dip["dip_start_s"]), 3),
                    "end_s": round(start + float(dip["dip_end_s"]), 3),
                    "origin": ORIGIN,
                }
            )
        if ident == "buildup_drop":
            timeline.append(
                {
                    "kind": "build",
                    "start_s": round(start + float(arc_marks["build_start_s"]), 3),
                    "end_s": round(start + float(arc_marks["drop_start_s"]), 3),
                    "origin": ORIGIN,
                }
            )
            timeline.append(
                {
                    "kind": "drop",
                    "start_s": round(start + float(arc_marks["drop_start_s"]), 3),
                    "end_s": round(start + float(arc_marks["drop_end_s"]), 3),
                    "origin": ORIGIN,
                }
            )
    peak = float(np.max(np.abs(program))) or 1.0
    program = np.clip(program / peak * 0.9, -1.0, 1.0)
    manifest: dict[str, object] = {
        "generator": GENERATOR,
        "origin": ORIGIN,
        "comparison_only": True,
        "injected_into_engine": False,
        "sample_rate": rate,
        "duration_s": round(program.size / rate, 3),
        "events": timeline,
    }
    return program.astype(np.float32), manifest


def write_program(directory: Path, rate: int = RATE) -> tuple[Path, Path]:
    """Write program.wav, manifest.json and the license next to them."""
    directory.mkdir(parents=True, exist_ok=True)
    samples, manifest = build_program(rate)
    audio = directory / "program.wav"
    meta = directory / "manifest.json"
    license_path = directory / "LICENSE.txt"
    _write_wav(audio, samples, rate)
    meta.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    license_path.write_text(
        "\n".join(
            [
                "ORNG show-bench corpus",
                f"generator: {GENERATOR}",
                "origin: original-synthesis",
                "license: CC0. Written for this bench. Not a published recording.",
                "The JSON manifest is a comparison score. It is not fed to the engine.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return audio, meta


def _write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    import wave

    pcm = np.clip(samples, -1.0, 1.0)
    ints = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(ints.tobytes())
