"""Dump Mix catalog frames from the show renderer. Does not touch a venue."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from orng_led.presets.catalog_clip import (
    CATALOG_VERSION,
    CLIP_FPS,
    WRAP_PAD_S,
    episode_stamp,
    recorded_duration_s,
    render_mix_catalog_frames,
    scene_stamp,
)
from orng_led.presets.store import PresetStore


def main() -> None:
    scene_dir = ROOT / "config" / "scenes" / "orng-bar"
    document = PresetStore.load(scene_dir / "presets").get("Mix")
    frames, cycle = render_mix_catalog_frames(scene_dir)
    recorded = recorded_duration_s(cycle)
    payload = {
        "version": CATALOG_VERSION,
        "scene_stamp": scene_stamp(scene_dir),
        "episode_stamp": episode_stamp(document),
        "fps": CLIP_FPS,
        "seconds": float(cycle),
        "recorded_s": float(recorded),
        "wrap_pad_s": float(WRAP_PAD_S),
        "frames": frames,
    }
    dest = ROOT / "frontend" / "src" / "runtime" / "mixCatalogClip.json"
    dest.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        f"wrote {dest} frames={len(frames)} cycle={cycle} recorded={recorded} "
        f"bytes={dest.stat().st_size}"
    )


if __name__ == "__main__":
    main()
