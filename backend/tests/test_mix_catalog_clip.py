"""The catalog clip is a slice of the show renderer, not a separate picture."""

from __future__ import annotations

import json
from pathlib import Path

from orng_led.config import load_show_config
from orng_led.config.models import FixtureKind
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.engine import Engine
from orng_led.engine.intents import BeamIntent
from orng_led.presets.catalog_clip import (
    CLIP_FPS,
    WRAP_PAD_S,
    clip_index,
    episode_stamp,
    recorded_duration_s,
    render_mix_catalog_frames,
    scene_stamp,
)
from orng_led.presets.models import LookAction
from orng_led.presets.store import PresetStore
from orng_led.simulator.decode import decode_simulator_view

ROOT = Path(__file__).resolve().parents[2]
PACK_PATH = ROOT / "frontend" / "src" / "runtime" / "mixCatalogClip.json"
SCENE = ROOT / "config" / "scenes" / "orng-bar"


def _close(live: float, packed: float) -> None:
    assert abs(float(live) - float(packed)) <= 5e-4, (live, packed)


def _assert_frame(view: dict, packed: list, *, geometry: bool = True) -> None:
    bars, pars, beams = packed
    assert [item["id"] for item in view["bars"]] == [row[0] for row in bars]
    assert [item["id"] for item in view["pars"]] == [row[0] for row in pars]
    assert [item["id"] for item in view["beams"]] == [row[0] for row in beams]
    faces_ok = view["faces"] == [] or all(
        float(item.get("intensity") or 0) <= 0.02 for item in view["faces"]
    )
    assert faces_ok
    for item, row in zip(view["bars"], bars, strict=True):
        _close(item["dimmer"], row[1])
        _close(item["r"], row[2])
        _close(item["g"], row[3])
        _close(item["b"], row[4])
        assert len(item["segments"]) == len(row[5])
        for live, saved in zip(item["segments"], row[5], strict=True):
            _close(live, saved)
    for item, row in zip(view["pars"], pars, strict=True):
        _close(item["r"], row[1])
        _close(item["g"], row[2])
        _close(item["b"], row[3])
        _close(item["intensity"], row[4])
    for item, row in zip(view["beams"], beams, strict=True):
        assert float(item["tilt"]) <= 0.640001
        assert float(row[2]) <= 0.640001
        _close(item["dimmer"], row[3])
        _close(item["r"], row[4])
        _close(item["g"], row[5])
        _close(item["b"], row[6])
        if geometry and len(row) >= 14:
            _close(item["hit_x"], row[8])
            _close(item["hit_z"], row[9])
            _close(item["dir_x"], row[10])
            _close(item["dir_y"], row[11])
            _close(item["dir_z"], row[12])
            _close(item["throw"], row[13])


def test_mix_catalog_clip_matches_the_renderer() -> None:
    pack = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    document = PresetStore.load(SCENE / "presets").get("Mix")
    assert pack["version"] == "preset-catalog-2"
    assert pack["episode_stamp"] == episode_stamp(document)
    assert pack["scene_stamp"] == scene_stamp(SCENE)
    assert pack["seconds"] == 756.0
    assert pack["fps"] == CLIP_FPS
    assert pack["recorded_s"] == recorded_duration_s(756.0)
    assert abs(float(pack["wrap_pad_s"]) - WRAP_PAD_S) < 1e-9
    assert len(pack["frames"]) == int(round(recorded_duration_s(756.0) * CLIP_FPS))
    fresh, cycle = render_mix_catalog_frames(SCENE)
    assert cycle == 756.0
    assert fresh == pack["frames"]
    for start in (0.125, 57.0, 93.0, 576.5, 612.0, 738.0, 755.75, 757.5):
        index = clip_index(start, CLIP_FPS, len(fresh))
        assert index is not None, start
        assert fresh[index] == pack["frames"][index], start


def test_episode_stamp_sees_actions_and_cycle_beats() -> None:
    store = PresetStore.load(SCENE / "presets")
    document = store.get("Mix")
    base = episode_stamp(document)
    with_action = document.model_copy(deep=True)
    third = with_action.episodes[2]
    with_action.episodes[2] = third.model_copy(
        update={
            "actions": [
                LookAction(
                    id="par-only",
                    groups=["par"],
                    palette="cyan",
                    effect="static",
                    intensity=1.0,
                )
            ]
        }
    )
    assert episode_stamp(with_action) != base
    with_beats = document.model_copy(deep=True)
    card = with_beats.episodes[2]
    with_beats.episodes[2] = card.model_copy(update={"cycle_beats": 8})
    assert episode_stamp(with_beats) != base
    assert episode_stamp(with_beats) != episode_stamp(with_action)


def test_wrap_keeps_engine_phase_not_the_first_frames() -> None:
    pack = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    early_at = clip_index(1.5, CLIP_FPS, len(pack["frames"]))
    wrap_at = clip_index(757.5, CLIP_FPS, len(pack["frames"]))
    assert early_at is not None and wrap_at is not None
    early = pack["frames"][early_at]
    wrapped = pack["frames"][wrap_at]
    assert early != wrapped

    show = load_show_config(SCENE)
    for fixture in show.patch.fixtures:
        if fixture.kind is FixtureKind.BEAM:
            fixture.spatial.beam_calibration_confirmed = True
    program = PresetStore.load(SCENE / "presets").programs()["Mix"]
    engine = Engine(show=show, presets={"Mix": program}, active_preset_id="Mix")
    engine.set_blackout(False)
    engine.overlays.master_brightness = 1.0
    fps = 30
    dt = 1.0 / fps
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
    # 757.5 s aligns to both 30 FPS and the 8 FPS pack.
    target = 757.5
    steps = int(round(target * fps))
    last = None
    for _ in range(steps):
        last = engine.tick(dt_s=dt, wall_dt_s=dt)
    assert last is not None
    view = decode_simulator_view(show, last.frame).model_dump(mode="json")
    assert abs(engine.preset_elapsed_s - target) < 1e-6
    # Continuous 30 FPS engine at the wrap does not match the first-cycle picture.
    early_index = clip_index(1.5, CLIP_FPS, len(pack["frames"]))
    assert early_index is not None
    early_bars = pack["frames"][early_index][0]
    live_bars = [
        (
            item["id"],
            round(float(item["dimmer"]), 4),
            round(float(item["r"]), 4),
            round(float(item["g"]), 4),
            round(float(item["b"]), 4),
        )
        for item in view["bars"]
    ]
    packed_early = [(row[0], row[1], row[2], row[3], row[4]) for row in early_bars]
    assert live_bars != packed_early
    wrap_index = clip_index(757.5, CLIP_FPS, len(pack["frames"]))
    assert wrap_index is not None
    for row in pack["frames"][wrap_index][2]:
        assert len(row) >= 14
        assert float(row[2]) <= 0.640001
    for item in view["beams"]:
        assert float(item["tilt"]) <= 0.640001
        assert "hit_x" in item and "hit_z" in item
