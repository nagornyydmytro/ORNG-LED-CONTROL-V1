"""L011 staff presets P01–P10: structure, difference, motion and safety."""

from __future__ import annotations

import pytest

from orng_led.config import load_show_config
from orng_led.engine.beam import BeamMotionLimits
from orng_led.engine.engine import Engine
from orng_led.engine.intents import ParIntent
from orng_led.engine.layers import STROBE_HOLD_TIMEOUT_S, STROBE_MAX_HZ
from orng_led.engine.presets import CYCLE_DURATION_S, EPISODE_COUNT, EPISODE_DURATION_S
from orng_led.presets.io import default_presets_dir, load_preset, load_presets_dir
from orng_led.presets.program import YamlPresetProgram
from orng_led.presets.staff import STAFF_LABELS, STAFF_PRESET_IDS, all_staff_presets
from orng_led.presets.store import PresetStore


@pytest.fixture(scope="module")
def show():
    return load_show_config()


@pytest.fixture(scope="module")
def staff_docs():
    docs = load_presets_dir(default_presets_dir())
    return {preset_id: docs[preset_id] for preset_id in STAFF_PRESET_IDS if preset_id in docs}


def test_exactly_ten_staff_yaml_files(staff_docs) -> None:
    assert set(staff_docs) == set(STAFF_PRESET_IDS)
    assert len(staff_docs) == 10


@pytest.mark.parametrize("preset_id", STAFF_PRESET_IDS)
def test_staff_preset_schema_and_duration(preset_id: str, staff_docs) -> None:
    doc = staff_docs[preset_id]
    assert doc.id == preset_id
    assert doc.label == STAFF_LABELS[preset_id]
    assert doc.builtin is True
    assert doc.hardware_tuned is False
    assert len(doc.episodes) == EPISODE_COUNT
    assert all(ep.duration_s == EPISODE_DURATION_S for ep in doc.episodes)
    assert abs(doc.total_duration_s - CYCLE_DURATION_S) < 1e-9
    # Round-trip through model validation already done by loader.


@pytest.mark.parametrize("preset_id", STAFF_PRESET_IDS)
def test_staff_yaml_matches_catalog_builder(preset_id: str, staff_docs) -> None:
    built = all_staff_presets()[preset_id]
    on_disk = staff_docs[preset_id]
    assert on_disk.model_dump() == built.model_dump()


def _signature(doc) -> tuple:
    return tuple(
        (
            tuple(ep.groups),
            ep.palette,
            ep.effect,
            round(ep.speed, 2),
            round(ep.intensity, 2),
            ep.transition,
        )
        for ep in doc.episodes
    )


def test_presets_are_content_distinct(staff_docs) -> None:
    signatures = {preset_id: _signature(doc) for preset_id, doc in staff_docs.items()}
    assert len(set(signatures.values())) == 10
    # Not only speed/intensity multiples of the same recipe.
    compositions = {
        preset_id: tuple((tuple(ep.groups), ep.palette, ep.effect) for ep in doc.episodes)
        for preset_id, doc in staff_docs.items()
    }
    assert len(set(compositions.values())) == 10


@pytest.mark.parametrize("preset_id", STAFF_PRESET_IDS)
def test_episode_evolves_over_time(preset_id: str, show, staff_docs) -> None:
    program = YamlPresetProgram(staff_docs[preset_id])
    # Mid first episode vs later in same episode.
    early = program.evaluate(2.0, show)
    late = program.evaluate(14.0, show)
    assert early.fixtures and late.fixtures

    def fixture_levels(intent_map: dict) -> list[float]:
        levels: list[float] = []
        for intent in intent_map.values():
            if isinstance(intent, ParIntent):
                levels.append(intent.intensity)
            elif hasattr(intent, "dimmer"):
                levels.append(float(intent.dimmer))
            if hasattr(intent, "segments"):
                levels.extend(float(x) for x in intent.segments)
        return levels

    assert fixture_levels(early.fixtures) != fixture_levels(late.fixtures)


@pytest.mark.parametrize("preset_id", STAFF_PRESET_IDS)
def test_accelerated_full_cycle_review(preset_id: str, show, staff_docs) -> None:
    engine = Engine(show=show, presets={preset_id: YamlPresetProgram(staff_docs[preset_id])})
    engine.select_preset(preset_id, reset_clock=True)
    # 180s of show time in coarse steps; frame must remain in bounds.
    for _ in range(36):
        snap = engine.tick(dt_s=5.0)
        assert len(snap.frame) == 512
        assert all(0 <= v <= 255 for v in snap.frame)
    assert engine.preset_elapsed_s == pytest.approx(180.0)
    # Loop continues without crash.
    snap = engine.tick(dt_s=1.0)
    assert snap.preset_id == preset_id


@pytest.mark.parametrize("preset_id", ["P01", "P05", "P10"])
def test_cycle_boundary_beam_no_teleport(preset_id: str, show, staff_docs) -> None:
    engine = Engine(show=show, presets={preset_id: YamlPresetProgram(staff_docs[preset_id])})
    engine.select_preset(preset_id, reset_clock=True)
    engine.preset_elapsed_s = CYCLE_DURATION_S - 0.05
    # Warm beams to current targets.
    engine.tick(dt_s=0.0)
    limits = BeamMotionLimits()
    dt = 1.0 / 30.0
    max_pan = limits.max_pan_speed * dt + 1e-9
    max_tilt = limits.max_tilt_speed * dt + 1e-9
    prev = {fid: (st.pan, st.tilt) for fid, st in engine.beam_motion.items()}
    for _ in range(12):
        engine.tick(dt_s=dt)
        for fid, state in engine.beam_motion.items():
            if fid not in prev:
                prev[fid] = (state.pan, state.tilt)
                continue
            d_pan = abs(state.pan - prev[fid][0])
            d_tilt = abs(state.tilt - prev[fid][1])
            assert d_pan <= max_pan
            assert d_tilt <= max_tilt
            prev[fid] = (state.pan, state.tilt)


@pytest.mark.parametrize("preset_id", STAFF_PRESET_IDS)
def test_staff_intents_keep_strobe_in_unit_range(preset_id: str, show, staff_docs) -> None:
    """Preset pulse may drive mapped bar strobe_speed; Live FX hold limits stay fixed."""
    from orng_led.engine.intents import BarIntent

    program = YamlPresetProgram(staff_docs[preset_id])
    intent = program.evaluate(30.0, show)
    for fixture_intent in intent.fixtures.values():
        strobe = float(getattr(fixture_intent, "strobe", 0.0))
        assert 0.0 <= strobe <= 1.0
        if not isinstance(fixture_intent, BarIntent):
            assert strobe == 0.0
    assert STROBE_MAX_HZ == 4.0
    assert STROBE_HOLD_TIMEOUT_S == 8.0


def test_loop_edge_episodes_share_soft_bridge(staff_docs) -> None:
    """ep10 → ep1 uses soft/fade bridge and related palette family where practical."""
    for preset_id, doc in staff_docs.items():
        ep1, ep10 = doc.episodes[0], doc.episodes[-1]
        assert ep10.transition in {"soft", "fade"}
        assert ep1.transition in {"soft", "fade", "cut"}
        # Same primary group family or identical palette keeps loop readable.
        shared_groups = set(ep1.groups) & set(ep10.groups)
        assert shared_groups or ep1.palette == ep10.palette, preset_id


def test_store_loads_all_staff_into_engine(show) -> None:
    store = PresetStore.load(default_presets_dir())
    programs = store.programs()
    assert set(STAFF_PRESET_IDS).issubset(programs.keys())
    engine = Engine(show=show, presets=programs)
    for preset_id in STAFF_PRESET_IDS:
        engine.select_preset(preset_id, reset_clock=True)
        snap = engine.tick(dt_s=0.5)
        assert snap.preset_id == preset_id
        assert snap.episode_index == 0


def test_disk_files_named_by_id() -> None:
    for preset_id in STAFF_PRESET_IDS:
        path = default_presets_dir() / f"{preset_id}.yaml"
        assert path.exists()
        doc = load_preset(path)
        assert doc.id == preset_id
