"""Play one phonogram through the capture path and record the engine frame.

BPM, lock, phase and section are not written in. The comparison manifest
is only a label on the picture and a column in the notes.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

from orng_led.engine.clock import FRAME_DT, FPS
from orng_led.output.mock import MockTransport
from orng_led.show_bench.corpus import RATE, write_program
from orng_led.show_bench.render import HEIGHT, WIDTH, render_frame
from orng_led.simulator.decode import decode_simulator_view

REPO = Path(__file__).resolve().parents[3]
REPO_CONFIG = REPO / "config"
REPO_SCENE = REPO_CONFIG / "scenes" / "orng-bar"
ARMING = (
    "ORNG_OUTPUT_POLICY",
    "ORNG_KEEP_OUTPUT_ARMED",
    "ORNG_CLOUD_DEVICE_MODE",
    "ORNG_CLOUD_DEVICE_URL",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _tree_hash(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(directory).as_posix().encode())
            digest.update(_sha256(path).encode())
    return digest.hexdigest()


def _code_version() -> dict[str, object]:
    def run(*args: str) -> str:
        try:
            return subprocess.check_output(args, cwd=REPO, text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    head = run("git", "rev-parse", "HEAD")
    dirty = run("git", "status", "--porcelain")
    return {
        "commit": head,
        "dirty": bool(dirty),
        "status_sha256": hashlib.sha256(dirty.encode()).hexdigest() if dirty else "",
    }


def _load_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        count = handle.getnframes()
        raw = handle.readframes(count)
        channels = handle.getnchannels()
        width = handle.getsampwidth()
    if width != 2:
        raise RuntimeError(f"expected 16-bit wav, got {width * 8}")
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    return pcm, rate


def _score_at(events: list[dict[str, object]], clock_s: float) -> str:
    name = "gap"
    for event in events:
        if event.get("kind") != "piece":
            continue
        if float(event["start_s"]) <= clock_s < float(event["end_s"]):
            name = str(event["id"])
    for event in events:
        if event.get("kind") == "silence" and float(event["start_s"]) <= clock_s < float(event["end_s"]):
            return "silence"
        if event.get("kind") == "volume_dip" and float(event["start_s"]) <= clock_s < float(event["end_s"]):
            return f"{name} dip"
        if event.get("kind") == "build" and float(event["start_s"]) <= clock_s < float(event["end_s"]):
            return f"{name} build"
        if event.get("kind") == "drop" and float(event["start_s"]) <= clock_s < float(event["end_s"]):
            return f"{name} drop"
    return name


def open_stand() -> tuple[object, Path]:
    """Isolated scene copy, Mock, disarmed. Blackout stays down until the caller checks."""
    for key in ARMING:
        os.environ.pop(key, None)
    root = Path(tempfile.mkdtemp(prefix="orng-show-bench-"))
    shutil.copytree(REPO_CONFIG, root, dirs_exist_ok=True)
    scene = root / "scenes" / "orng-bar"
    if scene.resolve() == REPO_SCENE.resolve():
        raise RuntimeError("refusing to open the working scene")
    from orng_led.api.runtime import AppRuntime

    runtime = AppRuntime.create(autostart_loop=False, config_dir=scene)
    transport = runtime.output.transport
    if not isinstance(transport, MockTransport):
        raise RuntimeError(f"stand transport is {type(transport).__name__}, not Mock")
    if runtime.output.armed:
        raise RuntimeError("stand output is armed; refusing to lift blackout")
    if runtime.engine.overlays.blackout is not True:
        raise RuntimeError("stand did not start in blackout")
    runtime.engine.set_blackout(False)
    runtime.bpm.configure(
        enabled=True,
        scope="auto",
        palette="orange",
        cast="all",
        character="rhythmic",
        intensity="full",
        period="auto",
    )
    runtime.bpm._start_worker()
    if not runtime.bpm._worker_alive():
        runtime.bpm._stop_worker()
        raise RuntimeError("tempo worker did not start")
    return runtime, root


def _heard_line(runtime: object) -> tuple[str, dict[str, object]]:
    bpm = runtime.bpm
    follower = bpm.follower
    section = bpm.section
    episode = bpm.planner.current
    look = "" if episode is None else str(episode.look)
    locked = bool(follower.locked)
    tempo = None if not locked or follower.bpm is None else round(float(follower.bpm), 1)
    row = {
        "look": look,
        "section": str(section.section),
        "locked": locked,
        "bpm": tempo,
        "shown": round(float(section.shown), 3),
    }
    tempo_text = "---" if tempo is None else f"{tempo:.0f}"
    lock_text = "lock" if locked else "free"
    line = f"{row['section']} {lock_text} {tempo_text} {look}"
    return line, row


def _frame_levels(view: object) -> dict[str, float]:
    pars = [float(item.intensity) for item in view.pars if not item.face]
    bars = [float(item.dimmer) for item in view.bars]
    beams = []
    pans = {}
    tilts = {}
    for item in view.beams:
        beams.append(max(float(item.r), float(item.g), float(item.b)))
        pans[item.id] = round(float(item.pan), 4)
        tilts[item.id] = round(float(item.tilt), 4)
    return {
        "par": round(sum(pars) / len(pars), 4) if pars else 0.0,
        "bar": round(sum(bars) / len(bars), 4) if bars else 0.0,
        "beam": round(sum(beams) / len(beams), 4) if beams else 0.0,
        "pan_l": pans.get("beam_left", 0.0),
        "pan_r": pans.get("beam_right", 0.0),
        "tilt_l": tilts.get("beam_left", 0.0),
        "tilt_r": tilts.get("beam_right", 0.0),
    }


def _wait_ear(runtime: object, job_id: int) -> None:
    """Let this analysis land. The trace keeps eight windows, so length never signals a new one."""
    deadline = time.monotonic() + 0.9
    while time.monotonic() < deadline:
        runtime.bpm.poll(0.0)
        windows = runtime.bpm.diagnostics().get("windows") or []
        latest = windows[-1].get("window") if windows else None
        if isinstance(latest, int) and latest >= job_id:
            return
        time.sleep(0.01)


def run_session(
    samples: np.ndarray,
    rate: int,
    *,
    mode: str,
    out_dir: Path,
    manifest: dict[str, object],
    limit_s: float | None = None,
    video: bool = True,
) -> dict[str, object]:
    if mode not in {"offline", "realtime"}:
        raise ValueError("mode must be offline or realtime")
    out_dir.mkdir(parents=True, exist_ok=True)
    runtime = None
    root: Path | None = None
    runtime, root = open_stand()
    transport = runtime.output.transport
    placements = {item.fixture_id: item for item in runtime.show.layout.placements}
    events = list(manifest.get("events") or [])
    hop = max(1, int(round(rate * FRAME_DT)))
    total = samples.size
    if limit_s is not None:
        total = min(total, int(limit_s * rate))
    encoder = _Encoder(out_dir / "picture.mp4", rate) if video else None
    trace_path = out_dir / "trace.jsonl"
    frames = 0
    dark = 0
    wall0 = time.monotonic()
    cursor = 0
    window_id = int(runtime.bpm._window_id)
    try:
        with trace_path.open("w", encoding="utf-8") as trace:
            while cursor < total:
                nxt = min(total, cursor + hop)
                block = samples[cursor:nxt]
                cursor = nxt
                before_id = int(runtime.bpm._window_id)
                runtime.bpm.submit_block(
                    block.astype(np.float64).tolist(),
                    rate,
                    captured_at=time.monotonic(),
                )
                if mode == "offline" and int(runtime.bpm._window_id) != before_id:
                    _wait_ear(runtime, int(runtime.bpm._window_id))
                    window_id = int(runtime.bpm._window_id)
                deadline = time.monotonic() + FRAME_DT
                snap = runtime.tick(FRAME_DT)
                frame = list(transport.frames[-1]) if transport.frames else list(snap.frame)
                transport.frames.clear()
                view = decode_simulator_view(runtime.show, frame)
                levels = _frame_levels(view)
                heard, row = _heard_line(runtime)
                clock_s = frames * FRAME_DT
                score = _score_at(events, clock_s)
                if levels["par"] < 0.04 and levels["bar"] < 0.04 and levels["beam"] < 0.04:
                    dark += 1
                trace.write(
                    json.dumps(
                        {"t": round(clock_s, 3), "score": score, **row, **levels},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                if encoder is not None:
                    encoder.write(render_frame(view, placements, clock_s=clock_s, score=score, heard=heard))
                frames += 1
                if mode == "realtime":
                    remain = deadline - time.monotonic()
                    if remain > 0:
                        time.sleep(remain)
        wall_s = time.monotonic() - wall0
        program_s = frames / FPS
        diagnostics = runtime.bpm.diagnostics()
        summary = {
            "mode": mode,
            "origin": manifest.get("origin"),
            "comparison_only": True,
            "injected_into_engine": False,
            "frames": frames,
            "fps": FPS,
            "program_s": round(program_s, 3),
            "wall_s": round(wall_s, 3),
            "dark_s": round(dark / FPS, 3),
            "transport": "mock",
            "armed": bool(runtime.output.armed),
            "blackout_lifted_in_stand": runtime.engine.overlays.blackout is False,
            "scene_id": runtime.scene_id,
            "preset_tree_sha256": _tree_hash(REPO_SCENE / "presets"),
            "code": _code_version(),
            "settings": {
                "scope": runtime.bpm.scope,
                "character": runtime.bpm.character,
                "intensity": runtime.bpm.intensity,
                "period": runtime.bpm.period,
                "palette": runtime.bpm.palette,
                "cast": runtime.bpm.cast,
            },
            "ear": {
                "worker": True,
                "analysis_jobs": int(runtime.bpm._window_id),
                "overflows": diagnostics.get("overflows"),
                "analysis_errors": diagnostics.get("analysis_errors"),
                "windows_kept": len(diagnostics.get("windows") or []),
                "last_window": (diagnostics.get("windows") or [None])[-1],
            },
            "video": None if encoder is None else str(encoder.path),
        }
        _notes(out_dir, summary, trace_path, events)
        return summary
    finally:
        if encoder is not None:
            wav = _trim_wav(samples[:cursor], rate, out_dir / "program-span.wav", frames) if frames else None
            encoder.close(program_wav=wav)
        if runtime is not None:
            runtime.bpm._stop_worker()
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)
        if out_dir.parent.name == "runs":
            _index(out_dir.parent.parent)


def _trim_wav(samples: np.ndarray, rate: int, path: Path, frames: int) -> Path:
    keep = min(samples.size, int(frames / FPS * rate))
    from orng_led.show_bench.corpus import _write_wav

    _write_wav(path, samples[:keep].astype(np.float32), rate)
    return path


def _notes(out_dir: Path, summary: dict[str, object], trace_path: Path, events: list[dict[str, object]]) -> None:
    rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line]
    looks: list[tuple[str, float]] = []
    for row in rows:
        look = str(row.get("look") or "")
        if not looks or looks[-1][0] != look:
            looks.append((look, float(row["t"])))
    holds = []
    for index, (look, start) in enumerate(looks):
        end = rows[-1]["t"] if index + 1 == len(looks) else looks[index + 1][1]
        holds.append((look or "—", round(end - start, 2)))
    pans = [abs(float(row["pan_l"]) - 0.5 + float(row["pan_r"]) - 0.5) for row in rows if row.get("pan_l") is not None]
    mirror = round(sum(pans) / len(pans), 4) if pans else None
    first_lock = next((row["t"] for row in rows if row.get("locked")), None)
    lines = [
        f"# Прогон {summary['mode']}",
        "",
        "Синтетический корпус. Разметка ниже — сравнение, её не передавали в движок.",
        "",
        "| | |",
        "| --- | --- |",
        f"| commit | `{summary['code']['commit']}` |",
        f"| dirty | {summary['code']['dirty']} |",
        f"| пресеты sha256 | `{summary['preset_tree_sha256']}` |",
        f"| кадры | {summary['frames']} |",
        f"| программа, с | {summary['program_s']} |",
        f"| стена, с | {summary['wall_s']} |",
        f"| темно, с | {summary['dark_s']} |",
        f"| первый lock, с | {first_lock} |",
        f"| среднее |pan_l-0.5 + pan_r-0.5| | {mirror} |",
        f"| задания анализа | {summary['ear']['analysis_jobs']} |",
        f"| overflows | {summary['ear']['overflows']} |",
        "",
        "## Удержания рисунка",
        "",
        "| рисунок | секунды |",
        "| --- | --- |",
    ]
    for look, seconds in holds:
        lines.append(f"| {look} | {seconds} |")
    lines.extend(["", "## Разметка для сравнения", ""])
    for event in events:
        if event.get("kind") in {"piece", "silence", "volume_dip", "build", "drop"}:
            lines.append(
                f"- {event.get('kind')} {event.get('id', '')} {event.get('start_s')}–{event.get('end_s')}"
            )
    (out_dir / "observations.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def _index(bench_root: Path) -> None:
    runs = bench_root / "runs"
    if not runs.is_dir():
        return
    cards = []
    for run_dir in sorted(path for path in runs.iterdir() if path.is_dir()):
        video = run_dir / "picture.mp4"
        notes = run_dir / "observations.md"
        if not video.is_file():
            continue
        body = notes.read_text(encoding="utf-8") if notes.is_file() else ""
        cards.append(
            "<article><h2>"
            + run_dir.name
            + "</h2><video controls src=\"runs/"
            + run_dir.name
            + "/picture.mp4\" width=\"640\"></video><pre>"
            + body.replace("&", "&amp;").replace("<", "&lt;")
            + "</pre></article>"
        )
    page = bench_root / "index.html"
    page.write_text(
        "\n".join(
            [
                "<!doctype html><meta charset=\"utf-8\"><title>ORNG show bench</title>",
                "<h1>Домашний стенд</h1>",
                "<p>Синтетический корпус. Кадр — декодированный выход движка на Mock. Разметка на видео — сравнение, не подставленный BPM.</p>",
                *cards,
            ]
        ),
        encoding="utf-8",
    )


class _Encoder:
    def __init__(self, path: Path, rate: int) -> None:
        self.path = path
        self.rate = rate
        self._proc: subprocess.Popen[bytes] | None = None
        self._audio: Path | None = None

    def write(self, frame: np.ndarray) -> None:
        if self._proc is None:
            self._proc = subprocess.Popen(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "-s",
                    f"{WIDTH}x{HEIGHT}",
                    "-r",
                    str(FPS),
                    "-i",
                    "-",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(self.path.with_suffix(".video.mp4")),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        assert self._proc.stdin is not None
        self._proc.stdin.write(np.ascontiguousarray(frame).tobytes())

    def close(self, program_wav: Path | None = None) -> None:
        if self._proc is None:
            return
        if self._proc.stdin is not None:
            self._proc.stdin.close()
        self._proc.wait(timeout=120)
        silent = self.path.with_suffix(".video.mp4")
        if program_wav is not None and program_wav.is_file() and silent.is_file():
            mux = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(silent),
                    "-i",
                    str(program_wav),
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(self.path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if mux.returncode == 0:
                silent.unlink(missing_ok=True)
            else:
                silent.replace(self.path)
        elif silent.is_file():
            silent.replace(self.path)


def ensure_corpus(directory: Path) -> tuple[np.ndarray, int, dict[str, object]]:
    audio, meta = write_program(directory)
    samples, rate = _load_wav(audio)
    manifest = json.loads(meta.read_text(encoding="utf-8"))
    return samples, rate, manifest
