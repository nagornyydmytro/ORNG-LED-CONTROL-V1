"""Write staff preset YAML files into config/presets/."""

from __future__ import annotations

from pathlib import Path

from orng_led.presets.io import default_presets_dir, save_preset
from orng_led.presets.staff import all_staff_presets


def write_staff_presets(directory: Path | None = None) -> list[Path]:
    root = directory or default_presets_dir()
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for preset_id, document in all_staff_presets().items():
        path = root / f"{preset_id}.yaml"
        save_preset(path, document)
        written.append(path)
    return written


if __name__ == "__main__":
    paths = write_staff_presets()
    for path in paths:
        print(path)
