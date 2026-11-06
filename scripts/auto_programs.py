"""Dump Auto director programs as a local HTML timeline.

Isolated. No Art-Net, no venue USB, no deploy.

    python scripts/auto_programs.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from orng_led.audio.programs import dump_html, programs, run_program  # noqa: E402
from orng_led.config.io import load_show_config  # noqa: E402


def main() -> None:
    show = load_show_config()
    fixtures = list(show.patch.fixtures)
    runs = [
        run_program(item, fixtures, show.layout.placements, step_s=0.5)
        for item in programs()
    ]
    path = Path.home() / "Desktop" / "orng-auto-programs.html"
    path.write_text(dump_html(runs), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
