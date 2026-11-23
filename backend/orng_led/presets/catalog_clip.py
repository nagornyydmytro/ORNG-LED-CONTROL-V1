"""Engine frames for the Mix catalog preview.

The cloud page has no show renderer. It may serve these frames only while the
open document, the scene mapping and this pack version still match. A draft
edit does not get the baked picture.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from orng_led.audio.preview import preview_source_text
from orng_led.presets.models import PresetDocument

# Bump when the packed frame layout, stamp rules or seek semantics change.
CATALOG_VERSION = "preset-catalog-2"
CLIP_FPS = 8
# Continuous overrun past one document cycle. Effect phase keeps accumulating,
# so the first frames must not be reused after the wrap.
WRAP_PAD_S = 20.0


def _num_text(value: object) -> str:
    return f"{float(value):.4f}"


def _stamp_value(value: object) -> object:
    """Canonical form shared with the TypeScript catalog stamp."""
    if isinstance(value, float):
        return _num_text(value)
    if isinstance(value, list):
        return [_stamp_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _stamp_value(item) for key, item in value.items()}
    return value


def episode_stamp(document: PresetDocument) -> str:
    """Identity of every card field that changes the rendered picture."""
    payload = [_stamp_value(episode.model_dump(mode="json")) for episode in document.episodes]
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{CATALOG_VERSION}\n{body}".encode("utf-8")).hexdigest()


def scene_stamp(scene_dir: Path) -> str:
    """Document text, mapping and pack version. Draft edits of Mix change this."""
    preset = (scene_dir / "presets" / "Mix.yaml").read_text(encoding="utf-8").replace("\r\n", "\n")
    blob = f"{CATALOG_VERSION}\n{preset}\n{preview_source_text(scene_dir)}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def recorded_duration_s(cycle_s: float) -> float:
    return float(cycle_s) + float(WRAP_PAD_S)


def clip_index(time_s: float, fps: int, count: int) -> int | None:
    """Frame after one tick at ``fps``. No wrap into the first cycle."""
    if count <= 0:
        return None
    index = int(round(float(time_s) * fps)) - 1
    if index < 0 or index >= count:
        return None
    return index


def _round(value: object) -> float:
    return round(float(value), 4)


def _compact_view(view: dict) -> list:
    for face in view.get("faces") or []:
        if float(face.get("intensity") or 0) > 0.02:
            raise RuntimeError(f"FACE is lit in the Mix clip: {face.get('id')}")
    bars = [
        [
            item["id"],
            _round(item["dimmer"]),
            _round(item["r"]),
            _round(item["g"]),
            _round(item["b"]),
            [_round(value) for value in item["segments"]],
        ]
        for item in view.get("bars") or []
    ]
    pars = [
        [
            item["id"],
            _round(item["r"]),
            _round(item["g"]),
            _round(item["b"]),
            _round(item["intensity"]),
        ]
        for item in view.get("pars") or []
    ]
    beams = []
    for item in view.get("beams") or []:
        tilt = float(item["tilt"])
        if tilt > 0.640001:
            raise RuntimeError(f"Mix clip tilt {tilt} exceeds the saved limit")
        beams.append(
            [
                item["id"],
                _round(item["pan"]),
                _round(item["tilt"]),
                _round(item["dimmer"]),
                _round(item["r"]),
                _round(item["g"]),
                _round(item["b"]),
                1 if item.get("shutter_open") else 0,
                _round(item.get("hit_x", 0.0)),
                _round(item.get("hit_z", 0.0)),
                _round(item.get("dir_x", 0.0)),
                _round(item.get("dir_y", -1.0)),
                _round(item.get("dir_z", 0.0)),
                _round(item.get("throw", 1.0)),
            ]
        )
    return [bars, pars, beams]


def render_mix_catalog_frames(scene_dir: Path) -> tuple[list[list], float]:
    """One continuous renderer pass through the cycle and the wrap pad."""
    from orng_led.config import load_show_config
    from orng_led.engine.beam import BeamMotionState
    from orng_led.engine.engine import Engine
    from orng_led.engine.intents import BeamIntent
    from orng_led.presets.store import PresetStore
    from orng_led.simulator.decode import decode_simulator_view

    show = load_show_config(scene_dir)
    store = PresetStore.load(scene_dir / "presets")
    program = store.programs()["Mix"]
    engine = Engine(show=show, presets=store.programs(), active_preset_id="Mix")
    engine.set_blackout(False)
    engine.overlays.master_brightness = 1.0
    cycle = float(program.total_duration_s)
    dt = 1.0 / CLIP_FPS
    count = int(round(recorded_duration_s(cycle) * CLIP_FPS))
    engine.preset_elapsed_s = 0.0
    engine._resync_phase_turns()
    held = engine.active_preset.evaluate(0.0, engine.show)
    for fixture_id, intent in held.fixtures.items():
        if isinstance(intent, BeamIntent) and intent.pan is not None and intent.tilt is not None:
            engine.beam_motion[fixture_id] = BeamMotionState(
                pan=float(intent.pan),
                tilt=float(intent.tilt),
                last_valid_pan=float(intent.pan),
                last_valid_tilt=float(intent.tilt),
            )
    frames: list[list] = []
    for _ in range(count):
        snap = engine.tick(dt_s=dt, wall_dt_s=dt)
        view = decode_simulator_view(show, snap.frame).model_dump(mode="json")
        frames.append(_compact_view(view))
    return frames, cycle
