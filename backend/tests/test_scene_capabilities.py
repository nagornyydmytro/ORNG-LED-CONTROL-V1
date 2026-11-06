"""A look may use only the current scene's fixtures and described channels.

An empty or incompatible group is an error in the document and an empty
result at runtime. Explicit all_rear stays valid. Frames may contain the
profile's FIXED bytes; they may not touch a neighbor address.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orng_led.audio.auto_show import build_auto_stage
from orng_led.cloud.scene_bundle import apply_release_safely
from orng_led.config.io import load_show_config_from_scene_dir, save_model
from orng_led.config.models import (
    AppConfig,
    ChannelDefinition,
    ChannelRole,
    ConfigError,
    FixtureInstance,
    FixtureKind,
    FixtureProfile,
    PatchDocument,
    Side,
    SpatialLayout,
    SpatialPlacement,
    StagePlacement,
)
from orng_led.engine.beam import BeamMotionState
from orng_led.engine.layers import OverlayState, StageIntent, compose_layers
from orng_led.engine.renderer import render_stage, safe_dark_frame
from orng_led.presets.models import EpisodeCard, LookAction, PresetDocument
from orng_led.presets.program import YamlPresetProgram, _selected_ids
from orng_led.scenes.capabilities import matches_groups, scene_capability_problems
from orng_led.scenes.drafts import overlay_scene_dir, write_draft_preset
from orng_led.scenes.models import SceneManifest
from orng_led.scenes.semantics import validate_scene_texts
from orng_led.setup.beam_calibration import BeamCalibrationTestSession, render_calibration_frame


def test_orng_bar_document_stays_usable() -> None:
    root = Path(__file__).resolve().parents[2] / "config" / "scenes" / "orng-bar"
    texts = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in root.rglob("*.yaml")
    }
    problems = validate_scene_texts(texts)
    assert [item for item in problems if "не має приладів" in item or "сегменти" in item] == []
    assert problems == []


def test_group_match_keeps_categories() -> None:
    outer_beam = ["beam", "outer", "left", "all_rear"]
    inner_beam = ["beam", "inner", "right", "all_rear"]
    outer_par = ["par", "outer", "left", "all_rear"]
    left_bar = ["bar", "left", "all_rear"]
    assert matches_groups(outer_beam, ["outer", "beam"]) is True
    assert matches_groups(inner_beam, ["outer", "beam"]) is False
    assert matches_groups(outer_par, ["outer", "beam"]) is False
    assert matches_groups(outer_par, ["left", "right"]) is True
    assert matches_groups(inner_beam, ["left", "right"]) is True
    assert matches_groups(outer_par, ["par", "bar"]) is True
    assert matches_groups(left_bar, ["par", "bar"]) is True
    assert matches_groups(outer_beam, ["par", "bar"]) is False
    assert matches_groups(outer_par, ["all_rear"]) is True
    assert matches_groups([], ["all_rear"]) is False
    assert matches_groups(["face"], ["all_rear"]) is False
    assert matches_groups(outer_par, []) is False
    assert matches_groups(left_bar, ["outer"]) is False


def _place(kind: FixtureKind, groups: list[str]) -> SpatialPlacement:
    side = Side.LEFT if "left" in groups or "right" not in groups else Side.RIGHT
    return SpatialPlacement(side=side, beam_calibration_confirmed=kind is FixtureKind.BEAM)


def _fixture(fixture_id: str, profile_id: str, kind: FixtureKind, address: int, groups: list[str]) -> FixtureInstance:
    return FixtureInstance(
        id=fixture_id,
        profile_id=profile_id,
        label=fixture_id,
        start_address=address,
        kind=kind,
        groups=groups,
        spatial=_place(kind, groups),
    )


def _dimmer(profile_id: str, kind: FixtureKind, *, fixed: int | None = None, footprint: int = 1) -> FixtureProfile:
    channels = [ChannelDefinition(local=1, role=ChannelRole.DIMMER)]
    if fixed is not None:
        channels.append(ChannelDefinition(local=3, role=ChannelRole.FIXED, fixed_value=fixed))
        footprint = max(footprint, 3)
    return FixtureProfile(
        id=profile_id,
        label=profile_id,
        kind=kind,
        footprint=footprint,
        channels=channels,
    )


def _write(root: Path, profiles: list[FixtureProfile], fixtures: list[FixtureInstance], episodes: list[EpisodeCard]) -> None:
    root.mkdir(parents=True)
    save_model(root / "scene.yaml", SceneManifest(scene_id=root.name, name=root.name))  # type: ignore[arg-type]
    save_model(
        root / "app.yaml",
        AppConfig(pad_presets=[f"P0{index}" for index in range(1, 10)]),
    )
    for profile in profiles:
        save_model(root / "profiles" / f"{profile.id}.yaml", profile)
    save_model(root / "patch.yaml", PatchDocument(fixtures=fixtures))
    save_model(
        root / "layout.yaml",
        SpatialLayout(
            fixtures=[item.id for item in fixtures],
            placements=[
                StagePlacement(fixture_id=item.id, kind=item.kind, x=0.2, y=0.4) for item in fixtures
            ],
        ),
    )
    from orng_led.presets.io import save_preset

    save_preset(root / "presets" / "P01.yaml", PresetDocument(id="P01", label="P01", episodes=episodes))
    filler = [EpisodeCard(id="wash", duration_s=4, groups=["all_rear"], effect="static", transition="cut")]
    for index in range(2, 10):
        save_preset(
            root / "presets" / f"P0{index}.yaml",
            PresetDocument(id=f"P0{index}", label=f"P0{index}", episodes=filler),
        )


def _texts(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in root.rglob("*.yaml")
    }


def _owned(show) -> set[int]:
    found: set[int] = set()
    for fixture in show.patch.fixtures:
        profile = show.profile_for(fixture)
        for local in range(1, profile.footprint + 1):
            found.add(fixture.start_address + local - 2)
    return found


def _contained(show, frame: list[int]) -> None:
    owned = _owned(show)
    outside = [index for index, value in enumerate(frame) if value and index not in owned]
    assert outside == []
    assert len(frame) == 512


def test_beam_on_a_par_scene_stays_empty(tmp_path: Path) -> None:
    root = tmp_path / "par-only"
    par = _fixture("par_1", "par", FixtureKind.PAR, 30, ["par", "left", "all_rear"])
    _write(
        root,
        [_dimmer("par", FixtureKind.PAR)],
        [par],
        [EpisodeCard(id="beam", duration_s=4, groups=["beam"], effect="static", transition="cut")],
    )
    show = load_show_config_from_scene_dir(root)
    card = EpisodeCard(id="beam", duration_s=4, groups=["beam"], effect="static", transition="cut")
    assert _selected_ids(card, show) == set()
    assert _selected_ids(
        EpisodeCard(id="rear", duration_s=4, groups=["all_rear"], effect="static", transition="cut"),
        show,
    ) == {"par_1"}
    problems = validate_scene_texts(_texts(root))
    assert "P01/beam: група beam не має приладів" in problems
    stage = YamlPresetProgram(
        PresetDocument(id="P01", label="P01", episodes=[card])
    ).evaluate(0.0, show)
    assert stage.fixtures == {}
    frame = render_stage(show, stage, {})
    _contained(show, frame)
    assert frame[29] == 0


def test_partial_group_selects_only_the_match(tmp_path: Path) -> None:
    root = tmp_path / "mixed"
    fixtures = [
        _fixture("par_outer", "par", FixtureKind.PAR, 1, ["par", "outer", "left", "all_rear"]),
        _fixture("beam_outer", "beam", FixtureKind.BEAM, 20, ["beam", "outer", "left", "all_rear"]),
        _fixture("beam_inner", "beam", FixtureKind.BEAM, 40, ["beam", "inner", "right", "all_rear"]),
        _fixture("bar_left", "bar", FixtureKind.BAR, 60, ["bar", "left", "all_rear"]),
    ]
    _write(
        root,
        [
            _dimmer("par", FixtureKind.PAR),
            _dimmer("beam", FixtureKind.BEAM, footprint=2),
            _dimmer("bar", FixtureKind.BAR, fixed=243, footprint=4),
        ],
        fixtures,
        [EpisodeCard(id="outer-beam", duration_s=4, groups=["outer", "beam"], effect="static", transition="cut")],
    )
    show = load_show_config_from_scene_dir(root)
    card = EpisodeCard(id="outer-beam", duration_s=4, groups=["outer", "beam"], effect="static", transition="cut")
    assert _selected_ids(card, show) == {"beam_outer"}
    assert _selected_ids(
        EpisodeCard(id="sides", duration_s=4, groups=["left", "right"], effect="static", transition="cut"),
        show,
    ) == {"par_outer", "beam_outer", "beam_inner", "bar_left"}
    assert _selected_ids(
        EpisodeCard(id="wash", duration_s=4, groups=["par", "bar"], effect="static", transition="cut"),
        show,
    ) == {"par_outer", "bar_left"}
    assert "не має приладів" not in " ".join(validate_scene_texts(_texts(root)))


def test_missing_and_incomplete_capabilities(tmp_path: Path) -> None:
    root = tmp_path / "bars"
    profile = FixtureProfile(
        id="bar",
        label="bar",
        kind=FixtureKind.BAR,
        footprint=4,
        segment_count=4,
        channels=[
            ChannelDefinition(local=1, role=ChannelRole.DIMMER),
            ChannelDefinition(local=3, role=ChannelRole.FIXED, fixed_value=243),
            ChannelDefinition(local=4, role=ChannelRole.SEGMENT_COLOR, segment_index=1),
        ],
    )
    _write(
        root,
        [profile],
        [_fixture("bar_1", "bar", FixtureKind.BAR, 10, ["bar", "all_rear"])],
        [EpisodeCard(id="chase", duration_s=4, groups=["bar"], effect="chase", transition="cut")],
    )
    texts = _texts(root)
    problems = scene_capability_problems(
        {path: __import__("yaml").safe_load(text) for path, text in texts.items()}
    )
    assert "bar: сегменти описані неповністю" in problems
    assert any("потребує сегменти" in item for item in problems)
    show = load_show_config_from_scene_dir(root)
    stage = YamlPresetProgram(
        PresetDocument(
            id="P01",
            label="P01",
            episodes=[EpisodeCard(id="chase", duration_s=4, groups=["bar"], effect="chase", transition="cut")],
        )
    ).evaluate(0.0, show)
    assert "bar_1" not in stage.fixtures
    frame = render_stage(show, stage, {})
    _contained(show, frame)
    assert frame[9] == 0
    assert frame[11] == 243

    only_fixed = FixtureProfile(
        id="fixed",
        label="fixed",
        kind=FixtureKind.PAR,
        footprint=1,
        channels=[ChannelDefinition(local=1, role=ChannelRole.FIXED, fixed_value=17)],
    )
    fixed_root = tmp_path / "fixed-only"
    _write(
        fixed_root,
        [only_fixed],
        [_fixture("par_1", "fixed", FixtureKind.PAR, 50, ["par", "all_rear"])],
        [EpisodeCard(id="wash", duration_s=4, groups=["par"], effect="static", transition="cut")],
    )
    fixed_text = _texts(fixed_root)
    assert any(
        "не має каналу" in item
        for item in scene_capability_problems(
            {path: __import__("yaml").safe_load(text) for path, text in fixed_text.items()}
        )
    )
    fixed_show = load_show_config_from_scene_dir(fixed_root)
    skipped = YamlPresetProgram(
        PresetDocument(
            id="P01",
            label="P01",
            episodes=[EpisodeCard(id="wash", duration_s=4, groups=["par"], effect="static", transition="cut")],
        )
    ).evaluate(0.0, fixed_show)
    assert skipped.fixtures == {}
    parked = render_stage(fixed_show, skipped, {})
    _contained(fixed_show, parked)
    assert parked[49] == 17

    missing = dict(texts)
    del missing["profiles/bar.yaml"]
    assert any("невідомий профіль" in item for item in validate_scene_texts(missing))


def test_raw_document_names_an_empty_or_unknown_group() -> None:
    parsed = {
        "patch.yaml": {"fixtures": [{"id": "par_1", "kind": "par", "profile_id": "par", "groups": ["par"]}]},
        "profiles/par.yaml": {"kind": "par", "channels": [{"local": 1, "role": "dimmer"}]},
        "presets/P01.yaml": {"episodes": [{"id": "blank", "groups": [], "effect": "pulse"}]},
    }
    assert scene_capability_problems(parsed) == ["P01/blank: дія не називає групу"]
    parsed["presets/P01.yaml"]["episodes"][0]["groups"] = ["stage-left"]
    assert scene_capability_problems(parsed) == ["P01/blank: невідома група stage-left"]


def test_a_beam_without_axes_lights_only_its_dimmer(tmp_path: Path) -> None:
    root = tmp_path / "head"
    _write(
        root,
        [_dimmer("beam", FixtureKind.BEAM)],
        [_fixture("beam_1", "beam", FixtureKind.BEAM, 80, ["beam", "all_rear"])],
        [EpisodeCard(id="wash", duration_s=4, groups=["beam"], effect="static", intensity=1, transition="cut")],
    )
    show = load_show_config_from_scene_dir(root)
    stage = YamlPresetProgram(
        PresetDocument(
            id="P01",
            label="P01",
            episodes=[
                EpisodeCard(id="wash", duration_s=4, groups=["beam"], effect="static", intensity=1, transition="cut")
            ],
        )
    ).evaluate(0.0, show)
    assert set(stage.fixtures) == {"beam_1"}
    frame = render_stage(show, stage, {"beam_1": BeamMotionState.from_home(0.5, 0.5)})
    _contained(show, frame)
    assert frame[79] == 255
    assert frame[80] == 0
    assert "не має приладів" not in " ".join(validate_scene_texts(_texts(root)))


def test_frames_stay_inside_the_current_scene(tmp_path: Path) -> None:
    def scene(address: int) -> object:
        root = tmp_path / f"addr-{address}"
        bar = FixtureProfile(
            id="bar",
            label="bar",
            kind=FixtureKind.BAR,
            footprint=4,
            segment_count=1,
            channels=[
                ChannelDefinition(local=1, role=ChannelRole.DIMMER),
                ChannelDefinition(local=2, role=ChannelRole.SEGMENT_COLOR, segment_index=1),
                ChannelDefinition(local=3, role=ChannelRole.FIXED, fixed_value=243),
            ],
        )
        beam = FixtureProfile(
            id="beam",
            label="beam",
            kind=FixtureKind.BEAM,
            footprint=4,
            channels=[
                ChannelDefinition(local=1, role=ChannelRole.DIMMER),
                ChannelDefinition(local=2, role=ChannelRole.PAN_COARSE),
                ChannelDefinition(local=3, role=ChannelRole.TILT_COARSE),
                ChannelDefinition(local=4, role=ChannelRole.RED),
            ],
        )
        face = _dimmer("face", FixtureKind.FACE_PAR)
        _write(
            root,
            [_dimmer("par", FixtureKind.PAR), bar, beam, face],
            [
                _fixture("par_1", "par", FixtureKind.PAR, address, ["par", "left", "all_rear"]),
                _fixture("bar_1", "bar", FixtureKind.BAR, address + 20, ["bar", "left", "all_rear"]),
                _fixture("beam_1", "beam", FixtureKind.BEAM, address + 40, ["beam", "outer", "left", "all_rear"]),
                _fixture("face_1", "face", FixtureKind.FACE_PAR, address + 60, ["face"]),
            ],
            [
                EpisodeCard(id="wash", duration_s=4, groups=["par"], effect="static", intensity=1, transition="cut"),
                EpisodeCard(id="next", duration_s=4, groups=["par"], effect="static", intensity=0.4, transition="soft"),
            ],
        )
        return load_show_config_from_scene_dir(root)

    low = scene(10)
    high = scene(200)
    program = YamlPresetProgram(
        PresetDocument(
            id="P01",
            label="P01",
            episodes=[
                EpisodeCard(id="wash", duration_s=4, groups=["par"], effect="static", intensity=1, transition="cut"),
                EpisodeCard(id="next", duration_s=4, groups=["par"], effect="static", intensity=0.4, transition="soft"),
            ],
        )
    )
    preset = render_stage(low, program.evaluate(0.0, low), {})
    _contained(low, preset)
    assert preset[9] > 0
    assert preset[10] == 0
    moved = render_stage(high, program.evaluate(0.0, high), {})
    _contained(high, moved)
    assert moved[9] == 0
    assert moved[199] > 0

    blend = render_stage(low, program.evaluate(4.0, low), {})
    _contained(low, blend)
    assert blend[9] > 0
    assert "beam_1" not in program.evaluate(4.0, low).fixtures

    musical = YamlPresetProgram(
        PresetDocument(
            id="M01",
            label="M01",
            musical_version=1,
            episodes=[
                EpisodeCard(
                    id="pulse",
                    duration_s=8,
                    groups=["par"],
                    effect="wave",
                    intensity=1,
                    transition="cut",
                    cycle_beats=4,
                )
            ],
        )
    )
    beat_a = render_stage(low, musical.evaluate(0.0, low, musical_beats=0.0), {})
    beat_b = render_stage(low, musical.evaluate(0.0, low, musical_beats=2.0), {})
    _contained(low, beat_a)
    _contained(low, beat_b)
    assert beat_a[9] != beat_b[9]

    class _At:
        def __init__(self, fixture_id: str, x: float) -> None:
            self.fixture_id = fixture_id
            self.x = x
            self.y = 0.4
            self.z = 0.0

    placed = [
        _At(item.id, 0.5 if item.kind is FixtureKind.BEAM else 0.2) for item in low.patch.fixtures
    ]
    auto_empty = build_auto_stage(
        low.patch.fixtures,
        "heads",
        "groove",
        0.2,
        0.8,
        placements=placed,
        groups=("beam",),
    )
    assert set(auto_empty.fixtures) == {"beam_1"}
    auto_frame = render_stage(
        low,
        auto_empty,
        {"beam_1": BeamMotionState.from_home(0.5, 0.5)},
    )
    _contained(low, auto_frame)
    par_only = [item for item in low.patch.fixtures if item.kind is FixtureKind.PAR]
    assert build_auto_stage(par_only, "heads", "groove", 0.2, 0.8, groups=("beam",)).fixtures == {}

    strobe = compose_layers(
        StageIntent(),
        low,
        OverlayState(strobe_held=True, strobe_started_at=0.0, strobe_phase=0.0, strobe_speed=0.1),
        0.0,
    )
    strobe_frame = render_stage(low, strobe, {"beam_1": BeamMotionState.from_home(0.5, 0.5)})
    _contained(low, strobe_frame)
    assert "face_1" not in strobe.fixtures
    assert strobe_frame[9] > 0

    face = compose_layers(StageIntent(), low, OverlayState(face_on=True, face_brightness=1.0), 0.0)
    assert set(face.fixtures) == {"face_1"}
    face_frame = render_stage(low, face, {})
    _contained(low, face_frame)
    assert face_frame[low.patch.fixture("face_1").start_address - 1] > 0
    assert face_frame[9] == 0

    blackout = compose_layers(StageIntent(), low, OverlayState(blackout=True), 0.0)
    assert blackout.fixtures == {}
    dark = safe_dark_frame(low)
    _contained(low, dark)
    assert dark[low.patch.fixture("bar_1").start_address + 1] == 243
    assert any(dark)

    session = BeamCalibrationTestSession(active=True, fixture_id="beam_1", semantic_pan=0.8, semantic_tilt=0.2)
    calibrated = render_calibration_frame(low, session, blackout=False)
    beam = low.patch.fixture("beam_1")
    start = beam.start_address - 1
    assert any(calibrated[start : start + 4])
    assert calibrated[start - 1] == 0
    assert calibrated[start + 4] == 0
    for index, value in enumerate(calibrated):
        if start <= index < start + 4:
            continue
        assert value == 0


def test_incompatible_revision_does_not_replace_the_scene(tmp_path: Path) -> None:
    root = tmp_path / "scenes" / "room"
    par = _fixture("par_1", "par", FixtureKind.PAR, 30, ["par", "all_rear"])
    _write(
        root,
        [_dimmer("par", FixtureKind.PAR)],
        [par],
        [EpisodeCard(id="wash", duration_s=4, groups=["par"], effect="static", transition="cut")],
    )
    live_patch = (root / "patch.yaml").read_text(encoding="utf-8")
    write_draft_preset(
        tmp_path,
        "room",
        PresetDocument(
            id="P01",
            label="P01",
            episodes=[EpisodeCard(id="beam", duration_s=4, groups=["beam"], effect="static", transition="cut")],
        ),
        base_version=1,
    )
    packed = tmp_path / "packed"
    overlay_scene_dir(root, tmp_path, "room", packed)
    problems = validate_scene_texts(_texts(packed))
    assert "P01/beam: група beam не має приладів" in problems
    assert (root / "patch.yaml").read_text(encoding="utf-8") == live_patch
    assert "beam" not in (root / "presets" / "P01.yaml").read_text(encoding="utf-8")

    files = {path.relative_to(packed).as_posix(): path.read_bytes() for path in packed.rglob("*") if path.is_file()}
    final = tmp_path / "final"
    final.mkdir()
    (final / "keep.txt").write_text("stay", encoding="utf-8")
    with pytest.raises(ConfigError, match="не має приладів"):
        apply_release_safely(
            staging_dir=tmp_path / "staging",
            final_dir=final,
            files=files,
            runtime=object(),
        )
    assert (final / "keep.txt").read_text(encoding="utf-8") == "stay"
    assert not (tmp_path / "staging").exists()
