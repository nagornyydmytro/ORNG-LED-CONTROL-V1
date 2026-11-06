from pathlib import Path

from orng_led.api.runtime import AppRuntime
from orng_led.audio.auto_show import PALETTE_IDS, build_auto_stage
from orng_led.audio.palettes import COLOR_IDS, catalog, color_for_fixture, migrate_palette
from orng_led.audio.settings import colour_of, known_period
from orng_led.config.models import FixtureKind
from orng_led.engine.intents import BarIntent, Rgbw


class _Lamp:
    def __init__(self, ident: str, kind: FixtureKind, x: float = 0.5) -> None:
        self.id = ident
        self.kind = kind
        self.layout_x = x
        self.layout_y = 0.4
        self.spatial = type("S", (), {"segment_count": 8, "height": 0.3})()


def test_catalog_has_twelve_thoughtful_palettes() -> None:
    rows = catalog()
    assert len(rows) >= 12
    kinds = {row["kind"] for row in rows}
    assert {"mono", "dual", "warm", "cool", "contrast", "multi"} <= kinds
    wheel = {"red", "orange", "green", "blue", "white", "amber", "cyan", "purple"}
    for row in rows:
        assert row["slots"]
        for slot in row["slots"]:
            assert slot in wheel


def test_legacy_bars_and_heads_migrate_without_touching_colour_ids() -> None:
    assert migrate_palette("bars") == ("orange", "bars")
    assert migrate_palette("heads") == ("white", "heads")
    assert colour_of("bars") == "orange"
    assert colour_of("heads") == "white"
    assert colour_of("violet") == "violet"
    assert "ember" in COLOR_IDS
    assert "amber" in PALETTE_IDS


def test_spatial_palette_is_stable_across_phase() -> None:
    fixtures = [
        _Lamp("bar-l", FixtureKind.BAR, x=0.1),
        _Lamp("bar-r", FixtureKind.BAR, x=0.9),
        _Lamp("par-1", FixtureKind.PAR, x=0.2),
    ]
    left = color_for_fixture("contrast", kind=FixtureKind.BAR, x=0.1, bar_index=0)
    right = color_for_fixture("contrast", kind=FixtureKind.BAR, x=0.9, bar_index=1)
    assert left != right
    first = build_auto_stage(fixtures, "contrast", "groove", 0.1, 0.8, look="wash", beats=0.0)
    later = build_auto_stage(fixtures, "contrast", "groove", 0.8, 0.8, look="wash", beats=3.0)
    assert first.fixtures["bar-l"].color == later.fixtures["bar-l"].color
    assert first.fixtures["bar-r"].color == later.fixtures["bar-r"].color
    assert isinstance(first.fixtures["bar-l"], BarIntent)
    assert first.fixtures["bar-l"].color != first.fixtures["bar-r"].color


def test_kind_palette_does_not_mint_unwheelable_bar_colour() -> None:
    bar = color_for_fixture("ember", kind=FixtureKind.BAR, x=0.2, bar_index=0)
    par = color_for_fixture("ember", kind=FixtureKind.PAR, x=0.2, bar_index=0)
    assert bar == Rgbw(r=1.0, g=0.35, b=0.05, w=0.0)
    assert par.r == 1.0 and par.g == 0.0


def test_incompatible_period_and_heads_migrate_on_this_location(tmp_path: Path) -> None:
    assert known_period(0.5, "smooth") is None
    assert known_period(8, "impact") is None
    assert known_period(2, "smooth") == 2.0
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    runtime.apply_bpm_sync(enabled=True, scope="auto", character="rhythmic", period=0.5)
    runtime.apply_bpm_sync(character="smooth")
    assert runtime.bpm.status()["period"] == "auto"
    runtime.apply_bpm_sync(palette="heads")
    status = runtime.bpm.status()
    assert status["palette"] == "white"
    assert status["cast"] == "heads"
    saved = (tmp_path / "location-show" / f"{runtime.scene_id}.json").read_text(encoding="utf-8")
    assert "white" in saved
    assert "heads" in saved
