"""Portable Auto gallery from the real renderer. Not the operator desk."""

from __future__ import annotations

from pathlib import Path

from orng_led.audio.gallery import CHARACTER_LOOKS, FEATURED_LOOKS, dump_portable
from orng_led.audio.library import BY_ID
from orng_led.audio.programs import dump_html, program_by_id, programs, run_program
from orng_led.config.io import load_show_config


def test_portable_gallery_has_wave_hit_and_three_characters(tmp_path: Path) -> None:
    dest = tmp_path / "auto-gallery.html"
    dump_portable(dest, frames=8)
    text = dest.read_text(encoding="utf-8")
    assert dest.stat().st_size > 2000
    assert "vertical_wave" in text
    assert "hit_bars" in text
    assert "cascade_up" in text
    assert "pulse_by_y" in text
    assert "script" in text
    assert "analysis" in text
    assert "сценарн" in text.lower() or "script" in text
    for look in FEATURED_LOOKS:
        assert look in BY_ID
        assert look in text
    for character, look in CHARACTER_LOOKS.items():
        assert character in text
        assert look in text
    assert CHARACTER_LOOKS["impact"] == "hit_bars"
    assert BY_ID[CHARACTER_LOOKS["impact"]].family == "hit_decay"


def test_director_programs_label_script_apart_from_analysis(tmp_path: Path) -> None:
    show = load_show_config()
    runs = [
        run_program(
            program_by_id(item.id),
            list(show.patch.fixtures),
            show.layout.placements,
            seed=7,
            step_s=1.0,
        )
        for item in programs()
        if item.id != "rise_accent"
    ]
    html = dump_html(runs)
    path = tmp_path / "auto-programs.html"
    path.write_text(html, encoding="utf-8")
    assert "script" in html
    assert "analysis" in html
    assert "сценарная секция" in html
    assert "распознавание аудио" in html
