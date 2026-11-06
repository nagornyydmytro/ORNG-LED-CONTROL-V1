"""Dump canonical Auto preview clips for the cloud worker. Not a live take."""

from __future__ import annotations

from pathlib import Path

from orng_led.audio.preview import dump_preview_pack


def main() -> None:
    dest = Path(__file__).resolve().parents[1] / "cloud" / "src" / "scenes" / "autoPreviewClips"
    path = dump_preview_pack(dest)
    files = list(path.glob("*.json.gz"))
    size = sum(item.stat().st_size for item in files)
    print(f"wrote {path} ({len(files)} looks, {size} bytes)")


if __name__ == "__main__":
    main()
