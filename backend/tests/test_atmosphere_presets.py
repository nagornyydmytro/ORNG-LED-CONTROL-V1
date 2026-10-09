"""Near-static atmosphere presets A01–A03: solid wash + one vertical bar episode."""

from __future__ import annotations

import pytest

from orng_led.config import load_show_config
from orng_led.config.models import DEFAULT_PAD_PRESETS
from orng_led.engine.intents import BarIntent
from orng_led.presets.atmosphere import (
    ATMOSPHERE_PRESET_IDS,
    _VERTICAL_EFFECTS,
    all_atmosphere_presets,
)
from orng_led.presets.io import default_presets_dir, load_presets_dir
from orng_led.presets.program import YamlPresetProgram


@pytest.fixture(scope="module")
def show():
    return load_show_config()


@pytest.fixture(scope="module")
def atmosphere_docs():
    docs = load_presets_dir(default_presets_dir())
    return {pid: docs[pid] for pid in ATMOSPHERE_PRESET_IDS if pid in docs}


def test_atmosphere_yaml_matches_builder(atmosphere_docs) -> None:
    built = all_atmosphere_presets()
    assert set(atmosphere_docs) == set(ATMOSPHERE_PRESET_IDS)
    for preset_id in ATMOSPHERE_PRESET_IDS:
        assert atmosphere_docs[preset_id].model_dump() == built[preset_id].model_dump()


def test_pad_starts_with_atmosphere_presets() -> None:
    assert list(DEFAULT_PAD_PRESETS[:3]) == ["A01", "A02", "A03"]


@pytest.mark.parametrize(
    ("preset_id", "effect"),
    [
        ("A01", "glow_line"),
        ("A02", "glow_pinch"),
        ("A03", "glow_eq"),
    ],
)
def test_exactly_one_vertical_episode(preset_id: str, effect: str, atmosphere_docs) -> None:
    doc = atmosphere_docs[preset_id]
    vertical = [ep for ep in doc.episodes if ep.effect in _VERTICAL_EFFECTS]
    assert len(vertical) == 1
    assert vertical[0].effect == effect
    assert vertical[0].id == "ep8"
    assert vertical[0].groups == ["bar"]
    assert all(ep.effect == "glow" for ep in doc.episodes if ep.id != "ep8")


@pytest.mark.parametrize("preset_id", ATMOSPHERE_PRESET_IDS)
def test_solid_episodes_keep_bars_whole(preset_id: str, show, atmosphere_docs) -> None:
    program = YamlPresetProgram(atmosphere_docs[preset_id])
    bar_ids = [f.id for f in show.patch.fixtures if f.id.startswith("bar_")]
    # Mid solid episode (ep1 ≈ 9s).
    intent = program.evaluate(9.0, show)
    for bar_id in bar_ids:
        bar = intent.fixtures.get(bar_id)
        assert isinstance(bar, BarIntent)
        assert bar.whole is True
        assert bar.dimmer >= 0.45


def test_a01_glow_line_is_sync_across_bars(show, atmosphere_docs) -> None:
    program = YamlPresetProgram(atmosphere_docs["A01"])
    bar_ids = [f.id for f in show.patch.fixtures if f.id.startswith("bar_")]
    # ep8 starts at 126s.
    intent = program.evaluate(132.0, show)
    patterns = []
    for bar_id in bar_ids:
        bar = intent.fixtures[bar_id]
        assert isinstance(bar, BarIntent)
        assert bar.whole is False
        lit = sum(1 for s in bar.segments if s >= 0.5)
        dark = sum(1 for s in bar.segments if s < 0.5)
        assert lit >= 2 and dark >= 2
        patterns.append(tuple(1 if s >= 0.5 else 0 for s in bar.segments))
    assert len(set(patterns)) == 1, f"bars not sync: {patterns}"


def test_a02_glow_pinch_starts_both_ends(show, atmosphere_docs) -> None:
    program = YamlPresetProgram(atmosphere_docs["A02"])
    bar_ids = [f.id for f in show.patch.fixtures if f.id.startswith("bar_")]
    # Exact start of ep8 — true bottom + true top together.
    intent = program.evaluate(126.0, show)
    patterns = []
    for bar_id in bar_ids:
        bar = intent.fixtures[bar_id]
        assert isinstance(bar, BarIntent)
        bits = tuple(1 if s >= 0.5 else 0 for s in bar.segments)
        patterns.append(bits)
        assert bits == (1, 0, 0, 0, 0, 0, 0, 1), f"{bar_id} open ends: {bits}"
    assert len(set(patterns)) == 1, f"pinch not sync: {patterns}"


def test_a02_glow_pinch_meets_as_pair(show, atmosphere_docs) -> None:
    """Even segment count: fronts meet as 4+4 (indices 3 and 4), no orphan dark."""
    program = YamlPresetProgram(atmosphere_docs["A02"])
    bar_ids = [f.id for f in show.patch.fixtures if f.id.startswith("bar_")]
    saw_full = False
    saw_three = False
    for t in (126.0, 126.5, 127.0, 127.5, 127.8, 128.5, 129.0):
        intent = program.evaluate(t, show)
        for bar_id in bar_ids:
            bar = intent.fixtures[bar_id]
            assert isinstance(bar, BarIntent)
            bits = tuple(1 if s >= 0.5 else 0 for s in bar.segments)
            assert bits == bits[::-1], f"{bar_id} @{t}s not mirror: {bits}"
            if bits == (1, 1, 1, 0, 0, 1, 1, 1):
                saw_three = True
            if bits == (1, 1, 1, 1, 1, 1, 1, 1):
                saw_full = True
    assert saw_three, "never saw 3+3 with dark meeting pair"
    assert saw_full, "never saw 4+4 full meet"


def test_a03_glow_eq_varies_across_bar_row(show, atmosphere_docs) -> None:
    program = YamlPresetProgram(atmosphere_docs["A03"])
    bar_ids = sorted(
        (f.id for f in show.patch.fixtures if f.id.startswith("bar_")),
        key=lambda fid: show.layout.placement_for(fid).x
        if show.layout.placement_for(fid) is not None
        else 0.0,
    )
    intent = program.evaluate(132.0, show)
    heights = []
    for bar_id in bar_ids:
        bar = intent.fixtures[bar_id]
        assert isinstance(bar, BarIntent)
        assert bar.whole is False
        # Bottom-up column: once off, stays off above.
        on = [s >= 0.5 for s in bar.segments]
        if False in on:
            first_off = on.index(False)
            assert all(not v for v in on[first_off:])
        heights.append(sum(1 for v in on if v))
    assert min(heights) >= 1
    assert len(set(heights)) >= 2, f"eq should vary across bars: {heights}"
