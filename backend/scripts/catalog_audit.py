"""Decoded-DMX catalog audit. Mock scene, no Art-Net, no publish.

One signature is the eight rendered frames of a composition, not its name.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from orng_led.audio.auto_show import build_auto_stage
from orng_led.audio.head_motion import HeadRig, prepare_head_travel, preview_heads
from orng_led.presets.io import default_presets_dir, load_presets_dir
from orng_led.audio.library import COMPOSITIONS, FAMILIES
from orng_led.audio.preview import _follow_preview_axes
from orng_led.config.io import load_show_config
from orng_led.config.schema import DMX_UNIVERSE_SIZE
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.frame import assert_frame_bounds
from orng_led.engine.renderer import render_stage
from orng_led.show_bench.render import render_frame
from orng_led.simulator.decode import decode_simulator_view

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "show-bench" / "runs" / "catalog"
PHASES = 8


def _signature(frames: list[list[int]]) -> tuple:
    return tuple(tuple(frame) for frame in frames)


def _lit(view) -> tuple[float, float, float]:
    pars = [float(item.intensity) for item in view.pars if not item.face]
    bars = [float(item.dimmer) for item in view.bars]
    beams = [max(float(item.r), float(item.g), float(item.b), float(item.dimmer)) for item in view.beams]
    return (
        max(pars) if pars else 0.0,
        max(bars) if bars else 0.0,
        max(beams) if beams else 0.0,
    )


def main() -> None:
    show = load_show_config()
    travel = prepare_head_travel(
        show,
        load_presets_dir(default_presets_dir()),
        scene_id=str(show.scene.id) if getattr(show, "scene", None) else "orng-bar",
        revision="catalog-audit",
    )
    placements = {item.fixture_id: item for item in show.layout.placements}
    fixtures = list(show.patch.fixtures)
    spans = []
    for fixture in fixtures:
        profile = show.profile_for(fixture)
        end = int(fixture.start_address) + int(profile.footprint) - 1
        spans.append((fixture.id, int(fixture.start_address), end, int(profile.footprint)))
        if end > DMX_UNIVERSE_SIZE or int(fixture.start_address) < 1:
            raise SystemExit(f"footprint {fixture.id} {fixture.start_address}+{profile.footprint}")
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    sheets: dict[str, list[np.ndarray]] = {family.id: [] for family in FAMILIES}
    clips: dict[str, list[np.ndarray]] = {family.id: [] for family in FAMILIES}
    signatures: dict[tuple, list[str]] = {}
    for composition in COMPOSITIONS:
        frames = []
        views = []
        pictures = []
        error = None
        rig = HeadRig()
        motion: dict[str, BeamMotionState] = {}
        try:
            cycle = 8.0 if composition.groups == ("beam",) else 4.0
            for index in range(PHASES):
                beats = (index / PHASES) * cycle
                stage = build_auto_stage(
                    fixtures,
                    "orange",
                    "groove",
                    0.0,
                    0.85,
                    look=composition.id,
                    beats=beats,
                    cycle_beats=cycle,
                    placements=show.layout.placements,
                    groups=composition.groups,
                )
                stage = preview_heads(
                    show,
                    stage,
                    beats=beats,
                    section="groove",
                    look=composition.id,
                    beat_period_s=0.5,
                    rig=rig,
                    travel=travel,
                    cycle_beats=cycle,
                )
                _follow_preview_axes(show, stage, motion, cycle / PHASES * 0.5)
                frame = render_stage(show, stage, motion)
                assert_frame_bounds(frame)
                frames.append(frame)
                view = decode_simulator_view(show, frame)
                views.append(view)
                pictures.append(render_frame(view, placements, clock_s=beats, score=composition.family, heard=composition.id))
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        levels = [_lit(view) for view in views]
        moving = len({tuple(round(channel / 8) for channel in frame) for frame in frames}) > 1
        dark = bool(levels) and max(max(item) for item in levels) < 0.02
        face = any(float(item.intensity) > 0.02 for view in views for item in view.faces)
        signature = _signature(frames) if frames else None
        if signature is not None:
            signatures.setdefault(signature, []).append(composition.id)
        if pictures:
            sheets[composition.family].append(pictures[2])
            clips[composition.family].extend(pictures)
        rows.append(
            {
                "id": composition.id,
                "family": composition.family,
                "groups": list(composition.groups),
                "error": error,
                "dark": dark,
                "moving": moving,
                "face": face,
                "peak": [round(max(axis), 3) for axis in zip(*levels)] if levels else [],
            }
        )
    twins = [names for names in signatures.values() if len(names) > 1]
    report = {
        "compositions": len(rows),
        "errors": [row for row in rows if row["error"]],
        "dark": [row["id"] for row in rows if row["dark"]],
        "still": [row["id"] for row in rows if not row["moving"] and not row["error"]],
        "face": [row["id"] for row in rows if row["face"]],
        "same_dmx": twins,
        "spans": spans,
    }
    (OUT / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for family, images in sheets.items():
        if not images:
            continue
        _sheet(images, OUT / f"{family}.png")
        _clip(clips[family], OUT / f"{family}.mp4")
    print(json.dumps({key: report[key] if key != "spans" else len(report["spans"]) for key in ("compositions", "errors", "dark", "still", "face", "same_dmx")}, ensure_ascii=False))


def _sheet(images: list[np.ndarray], path: Path) -> None:
    columns = 4
    cell_w, cell_h = 160, 90
    rows = (len(images) + columns - 1) // columns
    canvas = np.zeros((rows * cell_h, columns * cell_w, 3), dtype=np.uint8)
    for index, image in enumerate(images):
        small = image[::4, ::4]
        y, x = divmod(index, columns)
        canvas[y * cell_h : y * cell_h + small.shape[0], x * cell_w : x * cell_w + small.shape[1]] = small
    _png(canvas, path)


def _png(image: np.ndarray, path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{image.shape[1]}x{image.shape[0]}", "-i", "-", "-frames:v", "1", str(path)],
        input=np.ascontiguousarray(image).tobytes(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _clip(images: list[np.ndarray], path: Path) -> None:
    if not images:
        return
    height, width = images[0].shape[:2]
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", "4", "-i", "-", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert proc.stdin is not None
    for image in images:
        proc.stdin.write(np.ascontiguousarray(image).tobytes())
    proc.stdin.close()
    proc.wait(timeout=120)


if __name__ == "__main__":
    main()
