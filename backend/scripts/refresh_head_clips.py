"""Rewrite packed head clips with the current performer model.

Keeps the pack scene hash and library version so bar clips stay the same
snapshot. Does not publish a release.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

from orng_led.audio.library import BY_ID
from orng_led.audio.preview import _compact_frame, phase_frame_count, preview_clip
from orng_led.config.io import default_scene_dir, load_show_config


def preset_travel_hash(scene_dir: Path) -> str:
    parts: list[str] = []
    directory = scene_dir / "presets"
    paths = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
    for path in paths:
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        parts.append(f"presets/{path.name}\n{text}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def main() -> None:
    scene_dir = default_scene_dir()
    show = load_show_config(scene_dir)
    root = Path(__file__).resolve().parents[2] / "cloud" / "src" / "scenes" / "autoPreviewClips"
    written = 0
    for path in sorted(root.glob("*.json.gz")):
        look = path.name[: -len(".json.gz")]
        composition = BY_ID.get(look)
        if composition is None or "beam" not in composition.groups:
            continue
        pack = json.loads(gzip.decompress(path.read_bytes()))
        scene_hash = pack["scene_hash"]
        library = pack["library_version"]
        rebuilt: dict[str, dict[str, object]] = {}
        for key, row in pack["clips"].items():
            cycle = float(row["cycle_beats"])
            clip = preview_clip(
                show,
                look,
                cycle_beats=cycle,
                palette=str(row["palette"]),
                intensity=str(row["intensity"]),
                bpm=120.0,
                frames=phase_frame_count(cycle),
                scene_dir=scene_dir,
                scene_id="orng-bar",
            )
            if not clip.get("canonical"):
                raise SystemExit(f"{key} did not render: {clip.get('reason')}")
            rebuilt[key] = {
                "look": look,
                "cycle_beats": row["cycle_beats"],
                "loop_beats": clip["loop_beats"],
                "palette": row["palette"],
                "intensity": row["intensity"],
                "frames": [_compact_frame(frame) for frame in clip["frames"]],  # type: ignore[union-attr]
            }
            written += 1
            print(f"{written} {key} loop {clip['loop_beats']}", flush=True)
        payload = {
            "scene_hash": scene_hash,
            "library_version": library,
            "clips": rebuilt,
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        path.write_bytes(gzip.compress(raw, compresslevel=9))
    meta_path = root.parent / "autoPreviewPack.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["travel_hash"] = preset_travel_hash(scene_dir)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"travel {meta['travel_hash']}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
