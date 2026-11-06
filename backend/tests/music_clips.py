"""Legally obtained short music clips for home tempo checks.

Ground truth is the author's published tempo / duration math, not inspect_tempo.
Clips are cached outside the repo. This is not a USB venue take.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path

RATE = 44100

_CACHE = (
    Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    / "ORNG-LED-CONTROL"
    / "cache"
    / "music"
)

_HEADERS = {"User-Agent": "orng-led-control-home-tests/1.0 (legal CC0 fixture fetch)"}


@dataclass(frozen=True)
class ClipSpec:
    ident: str
    role: str
    title: str
    author: str
    license: str
    page: str
    published_bpm: float
    markup: str
    urls: tuple[str, ...]
    limitations: str


# Independent markup: filename/tags + 4 measures in 8.000 s → 16 beats / 8 s = 120 BPM.
DANCE_120 = ClipSpec(
    ident="dance_120_emsoundmoves",
    role="even_dance",
    title="Drumloop 003 tempo 120.wav",
    author="emsoundmoves",
    license="CC0-1.0",
    page="https://freesound.org/people/emsoundmoves/sounds/148620/",
    published_bpm=120.0,
    markup="Author tags/filename 120bpm; page duration 8.000 s for 4 measures of 4/4.",
    urls=(
        "https://cdn.freesound.org/previews/148/148620_2672718-hq.mp3",
        "https://freesound.org/data/previews/148/148620_2672718-hq.mp3",
    ),
    limitations=(
        "Freesound preview (MP3), not the original 16-bit WAV. Same piece and stated tempo. "
        "Not a DJ USB feed, not a venue PA, not a calibrated click."
    ),
)

# Second piece so the suite is not one-track. Author tags/title state 120 BPM.
HOUSE_128 = ClipSpec(
    ident="house_120_waveplay_035",
    role="dense_bed",
    title="[Drumloop] 120 bpm edm drum loop 035",
    author="waveplaySFX",
    license="CC0-1.0",
    page="https://freesound.org/people/waveplaySFX/sounds/546251/",
    published_bpm=120.0,
    markup="Author title/tags 120bpm; page duration 8.000 s. Different piece from 148620.",
    urls=(
        "https://cdn.freesound.org/previews/546/546251_1676145-hq.mp3",
        "https://freesound.org/data/previews/546/546251_1676145-hq.mp3",
    ),
    limitations=("CC0 house/EDM bed (preview MP3). Looped in tests. Not a USB venue feed."),
)


def cache_dir() -> Path:
    _CACHE.mkdir(parents=True, exist_ok=True)
    return _CACHE


def published_bpm_from_page_math(
    *, measures: float, seconds: float, beats_per_bar: float = 4.0
) -> float:
    """Independent tempo from the author's duration and form, not from our estimator."""
    beats = measures * beats_per_bar
    return beats / float(seconds) * 60.0


def _download(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        dest.write_bytes(response.read())


def _ffmpeg() -> str:
    return os.environ.get("ORNG_FFMPEG", "ffmpeg")


def _to_wav(src: Path, dest: Path) -> None:
    subprocess.check_call(
        [
            _ffmpeg(),
            "-y",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            str(RATE),
            "-sample_fmt",
            "s16",
            str(dest),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def ensure_clip(spec: ClipSpec) -> Path:
    dest = cache_dir() / f"{spec.ident}.wav"
    stamp = cache_dir() / f"{spec.ident}.source"
    marked = stamp.is_file() and stamp.read_text(encoding="utf-8").strip() in spec.urls
    if dest.is_file() and dest.stat().st_size > 8000 and marked:
        return dest
    errors: list[str] = []
    raw = cache_dir() / f"{spec.ident}.src"
    for url in spec.urls:
        try:
            _download(url, raw)
            if raw.stat().st_size < 2000:
                errors.append(f"{url}: too small")
                continue
            _to_wav(raw, dest)
            if dest.is_file() and dest.stat().st_size > 8000:
                stamp.write_text(url + "\n", encoding="utf-8")
                return dest
            errors.append(f"{url}: ffmpeg produced empty wav")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc}")
    raise FileNotFoundError(
        f"Could not fetch legal clip {spec.ident} ({spec.page}). Tried: {errors}"
    )


def read_mono(path: Path) -> list[float]:
    with wave.open(str(path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getframerate() == RATE
        width = handle.getsampwidth()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise ValueError(f"expected 16-bit PCM, got width={width}")
    samples = []
    for index in range(0, len(frames) - 1, 2):
        value = int.from_bytes(frames[index : index + 2], "little", signed=True)
        samples.append(value / 32768.0)
    return samples


def loop_to(samples: list[float], seconds: float) -> list[float]:
    need = int(seconds * RATE)
    if not samples:
        return [0.0] * need
    out: list[float] = []
    while len(out) < need:
        out.extend(samples)
    return out[:need]


def scale(samples: list[float], gain: float) -> list[float]:
    return [max(-1.0, min(1.0, value * gain)) for value in samples]


def sparse_phrase(
    samples: list[float], *, sound_s: float = 0.45, gap_s: float = 1.55
) -> list[float]:
    """Keep short onsets and insert silence. Grid stays the author's; density drops."""
    sound_n = max(1, int(sound_s * RATE))
    gap_n = max(1, int(gap_s * RATE))
    chunk = samples[:sound_n] if len(samples) >= sound_n else samples
    return (chunk + [0.0] * gap_n) * 8


def with_dropout(samples: list[float], *, silence_s: float) -> list[float]:
    gap = [0.0] * int(silence_s * RATE)
    return samples + gap + samples
