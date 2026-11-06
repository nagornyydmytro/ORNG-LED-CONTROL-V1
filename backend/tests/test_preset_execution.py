"""The editor document is the document the renderer runs.

Two action speeds keep two cycles when there is no BPM. A musical period
replaces that clock once. A fixture that appears or leaves changes level
across the existing cut / soft / fade windows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.audio.service import BpmService
from orng_led.config.io import load_show_config_from_scene_dir, save_model
from orng_led.config.models import (
    AppConfig,
    ChannelDefinition,
    ChannelRole,
    FixtureInstance,
    FixtureKind,
    FixtureProfile,
    PatchDocument,
    Side,
    SpatialLayout,
    SpatialPlacement,
    StagePlacement,
)
from orng_led.engine.engine import Engine
from orng_led.engine.layers import OverlayState, compose_layers
from orng_led.engine.renderer import render_stage
from orng_led.engine.timing import effect_rate_hz, unipolar_sine
from orng_led.main import create_app
from orng_led.presets.io import load_preset, save_preset
from orng_led.presets.models import EpisodeCard, LookAction, PresetDocument
from orng_led.presets.program import YamlPresetProgram, _blend_intent
from orng_led.scenes.models import SceneManifest
from orng_led.engine.intents import BeamIntent


def _place(kind: FixtureKind, groups: list[str]) -> SpatialPlacement:
    side = Side.RIGHT if "right" in groups else Side.LEFT
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


def _wash(profile_id: str, kind: FixtureKind) -> FixtureProfile:
    channels = [ChannelDefinition(local=1, role=ChannelRole.DIMMER)]
    footprint = 1
    if kind is FixtureKind.PAR:
        channels.append(ChannelDefinition(local=3, role=ChannelRole.FIXED, fixed_value=243))
        footprint = 3
    if kind is FixtureKind.BEAM:
        channels = [
            ChannelDefinition(local=1, role=ChannelRole.DIMMER),
            ChannelDefinition(local=2, role=ChannelRole.PAN_COARSE),
            ChannelDefinition(local=3, role=ChannelRole.TILT_COARSE),
        ]
        footprint = 3
    return FixtureProfile(id=profile_id, label=profile_id, kind=kind, footprint=footprint, channels=channels)


def _write_scene(
    root: Path,
    fixtures: list[FixtureInstance],
    profiles: list[FixtureProfile],
    document: PresetDocument,
    *,
    x: float = 0.0,
    pad: list[str] | None = None,
) -> None:
    root.mkdir(parents=True)
    save_model(root / "scene.yaml", SceneManifest(scene_id=root.name, name=root.name))
    save_model(root / "app.yaml", AppConfig(pad_presets=pad or [f"P0{index}" for index in range(1, 10)]))
    for profile in profiles:
        save_model(root / "profiles" / f"{profile.id}.yaml", profile)
    save_model(root / "patch.yaml", PatchDocument(fixtures=fixtures))
    save_model(
        root / "layout.yaml",
        SpatialLayout(
            fixtures=[item.id for item in fixtures],
            placements=[
                StagePlacement(fixture_id=item.id, kind=item.kind, x=x, y=0.4) for item in fixtures
            ],
        ),
    )
    save_preset(root / "presets" / f"{document.id}.yaml", document)


def _byte(show, fixture_id: str, frame: list[int], local: int = 1) -> int:
    fixture = show.patch.fixture(fixture_id)
    return frame[fixture.start_address + local - 2]


def _speed_show(tmp_path: Path):
    root = tmp_path / "scenes" / "speed"
    fixtures = [
        _fixture("left", "par", FixtureKind.PAR, 1, ["left", "par", "all_rear"]),
        _fixture("right", "par", FixtureKind.PAR, 10, ["right", "par", "all_rear"]),
    ]
    document = PresetDocument(
        id="M01",
        label="M01",
        musical_version=1,
        episodes=[
            EpisodeCard(
                id="mix",
                duration_s=30,
                groups=["all_rear"],
                effect="wave",
                speed=0.2,
                intensity=1,
                transition="cut",
                actions=[
                    LookAction(id="slow", groups=["left", "par"], effect="wave", speed=0.2, intensity=1, cycle_beats=4),
                    LookAction(id="fast", groups=["right", "par"], effect="wave", speed=0.8, intensity=1, cycle_beats=1),
                ],
            )
        ],
    )
    _write_scene(root, fixtures, [_wash("par", FixtureKind.PAR)], document, x=0.0)
    return load_show_config_from_scene_dir(root), YamlPresetProgram(document)


def test_two_speeds_differ_and_a_musical_period_is_applied_once(tmp_path: Path) -> None:
    show, program = _speed_show(tmp_path)
    slow = effect_rate_hz(0.2, "wave")
    fast = effect_rate_hz(0.8, "wave")
    moment = 0.25 / slow
    assert program.effect_turns(moment) == pytest.approx(0.25)

    start = program.evaluate(0.0, show)
    later = program.evaluate(moment, show)
    assert start.fixtures["left"].intensity == pytest.approx(start.fixtures["right"].intensity)
    assert later.fixtures["left"].intensity == pytest.approx(unipolar_sine(0.25))
    assert later.fixtures["right"].intensity == pytest.approx(unipolar_sine(0.25 * fast / slow))
    assert later.fixtures["left"].intensity != pytest.approx(later.fixtures["right"].intensity, abs=0.05)

    quiet = program.evaluate(0.0, show, effect_turns=99.0, musical_beats=0.5)
    loud = program.evaluate(0.0, show, effect_turns=0.0, musical_beats=0.5)
    assert quiet.fixtures["left"].intensity == pytest.approx(loud.fixtures["left"].intensity)
    assert quiet.fixtures["right"].intensity == pytest.approx(loud.fixtures["right"].intensity)
    assert quiet.fixtures["left"].intensity == pytest.approx(unipolar_sine(0.5 / 4))
    assert quiet.fixtures["right"].intensity == pytest.approx(unipolar_sine(0.5 / 1))

    engine = Engine(show=show, presets={"M01": program}, active_preset_id="M01")
    engine.musical_beats = 4.0
    engine.tick(dt_s=0.05)
    assert engine.effect_phase_turns == pytest.approx(4.0 / 4.0)
    elapsed = engine.preset_elapsed_s
    engine.musical_beats = None
    parked = engine.effect_phase_turns
    engine.tick(dt_s=0.05)
    assert engine.preset_elapsed_s == pytest.approx(elapsed + 0.05)
    assert engine.effect_phase_turns == pytest.approx(parked + slow * 0.05)
    assert engine.effect_phase_turns != pytest.approx(0.0)


def test_transition_frames_for_cut_soft_fade_and_a_short_episode(tmp_path: Path) -> None:
    root = tmp_path / "scenes" / "wash"
    fixtures = [
        _fixture("left", "par", FixtureKind.PAR, 1, ["left", "par", "all_rear"]),
        _fixture("right", "par", FixtureKind.PAR, 10, ["right", "par", "all_rear"]),
        _fixture("bar", "bar", FixtureKind.BAR, 20, ["bar", "all_rear"]),
        _fixture("beam", "beam", FixtureKind.BEAM, 40, ["beam", "all_rear"]),
    ]
    document = PresetDocument(
        id="T01",
        label="T01",
        episodes=[
            EpisodeCard(id="full", duration_s=8, groups=["par"], effect="static", intensity=1, transition="cut"),
            EpisodeCard(id="dim", duration_s=8, groups=["par"], effect="static", intensity=0.2, transition="fade"),
            EpisodeCard(id="left", duration_s=8, groups=["left", "par"], effect="static", intensity=1, transition="soft"),
            EpisodeCard(id="bars", duration_s=8, groups=["bar"], effect="static", intensity=1, transition="soft"),
            EpisodeCard(id="short", duration_s=1, groups=["par"], effect="static", intensity=1, transition="fade"),
            EpisodeCard(id="heads", duration_s=8, groups=["beam"], effect="static", intensity=1, transition="cut"),
        ],
    )
    _write_scene(root, fixtures, [_wash("par", FixtureKind.PAR), _wash("bar", FixtureKind.BAR), _wash("beam", FixtureKind.BEAM)], document)
    show = load_show_config_from_scene_dir(root)
    program = YamlPresetProgram(document)

    def frame_at(time_s: float) -> list[int]:
        return render_stage(show, program.evaluate(time_s, show), {})

    same = [frame_at(time_s) for time_s in (8.0, 9.0, 10.0)]
    same_levels = [_byte(show, "left", frame) for frame in same]
    assert same_levels[0] > 200
    assert same_levels[1] == pytest.approx(round(0.6 * 255), abs=1)
    assert same_levels[2] == pytest.approx(round(0.2 * 255), abs=1)
    assert [_byte(show, "right", frame) for frame in same] == same_levels

    partial = [frame_at(time_s) for time_s in (16.0, 17.5, 19.0)]
    assert _byte(show, "right", partial[0]) > _byte(show, "right", partial[1]) > _byte(show, "right", partial[2])
    assert _byte(show, "right", partial[2]) == 0
    assert _byte(show, "left", partial[2]) > 200
    assert _byte(show, "left", partial[1]) > _byte(show, "right", partial[1])

    swap = [frame_at(time_s) for time_s in (24.0, 25.5, 27.0)]
    leaving = [_byte(show, "left", frame) for frame in swap]
    arriving = [_byte(show, "bar", frame) for frame in swap]
    assert leaving[0] > leaving[1] > leaving[2] == 0
    assert arriving[0] < arriving[1] < arriving[2]
    assert arriving[2] > 200
    assert all(frame[3] == 0 and frame[18] == 0 and frame[20] == 0 for frame in swap)
    assert all(_byte(show, "left", frame, local=3) == 243 for frame in swap)
    assert all(_byte(show, "right", frame, local=3) == 243 for frame in (*same, *partial, *swap))

    opening = frame_at(32.0)
    finished = frame_at(32.99)
    assert _byte(show, "bar", opening) > 200
    assert _byte(show, "bar", finished) < 20
    assert _byte(show, "left", opening) < 20
    assert _byte(show, "left", finished) > 200

    cut = frame_at(33.0)
    assert _byte(show, "left", cut) == 0
    assert _byte(show, "bar", cut) == 0
    assert _byte(show, "beam", cut, local=1) == 255
    assert _byte(show, "beam", cut, local=2) == 128
    assert cut[38] == 0 and cut[42] == 0

    faded = _blend_intent(BeamIntent(pan=0.8, tilt=0.4, dimmer=1.0, shutter_open=True), None, 0.8)
    assert isinstance(faded, BeamIntent)
    assert faded.dimmer == 0.0
    assert faded.pan == pytest.approx(0.8)
    assert faded.tilt == pytest.approx(0.4)

    loop = YamlPresetProgram(
        PresetDocument(
            id="L01",
            label="L01",
            episodes=[
                EpisodeCard(id="pars", duration_s=8, groups=["par"], effect="static", intensity=1, transition="soft"),
                EpisodeCard(id="heads", duration_s=8, groups=["beam"], effect="static", intensity=1, transition="cut"),
            ],
        )
    )
    wrapped = [render_stage(show, loop.evaluate(time_s, show), {}) for time_s in (0.0, 1.2, 1.8)]
    assert [ _byte(show, "beam", frame, local=1) for frame in wrapped ] == [255, 255, 0]
    assert [ _byte(show, "beam", frame, local=2) for frame in wrapped ] == [128, 128, 128]
    assert _byte(show, "left", wrapped[0]) < _byte(show, "left", wrapped[1]) < _byte(show, "left", wrapped[2])

    engine = Engine(show=show, presets={"T01": program}, active_preset_id="T01")
    engine.seek_episode(1)
    assert engine.preset_elapsed_s == pytest.approx(8.0)
    sought = render_stage(
        show,
        program.evaluate(engine.preset_elapsed_s, show, effect_turns=engine.effect_phase_turns),
        engine.beam_motion,
    )
    assert _byte(show, "left", sought) > 200

    bright = program.evaluate(0.0, show)
    dim = program.evaluate(10.0, show)
    overlays = OverlayState(strobe_held=True, strobe_started_at=0.0)
    strobe_a = compose_layers(bright, show, overlays, 0.2)
    strobe_b = compose_layers(dim, show, overlays, 0.2)
    assert bright.fixtures["left"].intensity != pytest.approx(dim.fixtures["left"].intensity)
    assert strobe_a.fixtures["left"].intensity == pytest.approx(strobe_b.fixtures["left"].intensity)
    assert strobe_a.fixtures["left"].color == strobe_b.fixtures["left"].color
    assert strobe_a.fixtures["left"].color != dim.fixtures["left"].color


def test_preview_uses_the_engine_clock_and_does_not_arm(tmp_path: Path) -> None:
    root = tmp_path / "scenes" / "preview"
    fixtures = [_fixture("left", "par", FixtureKind.PAR, 1, ["par", "all_rear"])]
    document = PresetDocument(
        id="P01",
        label="P01",
        episodes=[EpisodeCard(id="wash", duration_s=8, groups=["par"], effect="static", intensity=1, transition="cut", notes="залишити")],
    )
    _write_scene(root, fixtures, [_wash("par", FixtureKind.PAR)], document)
    for index in range(2, 10):
        save_preset(
            root / "presets" / f"P0{index}.yaml",
            PresetDocument(id=f"P0{index}", label=f"P0{index}", episodes=[EpisodeCard(id="wash", duration_s=4, transition="cut")]),
        )
    runtime = AppRuntime.create(autostart_loop=False, config_dir=root)
    assert runtime.output.armed is False
    clip = runtime.preview_clip("P01", seconds=1.0, fps=4)
    assert clip["playback"] == "engine"
    assert clip["transport"] == "none"
    assert runtime.output.armed is False
    assert runtime.output.transport_kind.value != "artnet"

    program = runtime.preset_store.programs()["P01"]
    engine = Engine(show=runtime.show, presets={"P01": program}, active_preset_id="P01")
    snap = engine.tick(dt_s=0.25, wall_dt_s=0.25)
    direct = render_stage(
        runtime.show,
        program.evaluate(
            engine.preset_elapsed_s,
            runtime.show,
            effect_turns=engine.effect_phase_turns,
            movement_turns=engine.movement_phase_turns,
        ),
        engine.beam_motion,
    )
    assert snap.frame == direct

    runtime.start_editor_episode_preview(
        preset_id="P01",
        preset_label="Чернетка",
        episode_index=0,
        episode={"id": "wash", "duration_s": 8, "groups": ["par"], "effect": "static", "transition": "cut"},
    )
    assert runtime.output.armed is False
    assert runtime.output.transport_kind.value != "artnet"
    runtime.stop_editor_episode_preview()


def test_notes_survive_opening_and_a_copy_cannot_replace_p01(tmp_path: Path) -> None:
    root = tmp_path / "scenes" / "room"
    fixtures = [_fixture("left", "par", FixtureKind.PAR, 1, ["par", "all_rear"])]
    document = PresetDocument(
        id="P01",
        label="P01",
        episodes=[EpisodeCard(id="ep", duration_s=4, notes="залишити", transition="cut", cycle_beats=4)],
    )
    pad = ["C01", *[f"P0{index}" for index in range(1, 9)]]
    _write_scene(root, fixtures, [_wash("par", FixtureKind.PAR)], document, pad=pad)
    for index in range(2, 10):
        save_preset(
            root / "presets" / f"P0{index}.yaml",
            PresetDocument(id=f"P0{index}", label=f"P0{index}", episodes=[EpisodeCard(id="ep", duration_s=4, transition="cut")]),
        )
    save_preset(
        root / "presets" / "C01.yaml",
        PresetDocument(id="C01", label="C01", episodes=[EpisodeCard(id="ep", duration_s=4, transition="cut")]),
    )
    original = (root / "presets" / "P01.yaml").read_bytes()
    loaded = load_preset(root / "presets" / "P01.yaml")
    assert loaded.episodes[0].notes == "залишити"
    assert loaded.episodes[0].cycle_beats == 4
    assert (root / "presets" / "P01.yaml").read_bytes() == original

    runtime = AppRuntime.create(autostart_loop=False, config_dir=root)
    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app) as client:
        opened = client.get("/api/scene-draft/presets/P01")
        assert opened.status_code == 200
        assert (root / "presets" / "P01.yaml").read_bytes() == original
        refused = client.post(
            "/api/scene-draft/presets/P02/duplicate",
            json={"new_id": "P01", "base_version": 1},
        )
        assert refused.status_code == 400
        assert (root / "presets" / "P01.yaml").read_bytes() == original
        deleted = client.request(
            "DELETE",
            "/api/scene-draft/presets/C01",
            json={"base_version": 1},
        )
        assert deleted.status_code == 200
        copied = client.post(
            "/api/scene-draft/presets/P01/duplicate",
            json={"new_id": "C02", "label": "Копія", "base_version": 1},
        )
        assert copied.status_code == 200
        assert (root / "presets" / "P01.yaml").read_bytes() == original
    copy = load_preset(tmp_path / "drafts" / "room" / "presets" / "C02.yaml")
    assert copy.id == "C02"
    assert copy.episodes[0].notes == "залишити"
    assert copy.episodes[0].cycle_beats == 4
    pad_text = (tmp_path / "drafts" / "room" / "app.yaml").read_text(encoding="utf-8")
    assert "C01" not in pad_text
    assert "P09" in pad_text
    assert (root / "presets" / "P01.yaml").read_bytes() == original


def test_a_musical_bar_lands_on_the_next_entrance(tmp_path: Path) -> None:
    service = BpmService(tmp_path, "room")
    service.enabled = True
    service.mode = "bar"
    service.scope = "preset"
    service.follower.bpm = 120.0
    service.follower.locked = True
    service.follower._started = True
    service.follower._at = service._now
    service.follower._beats = 0.0
    opening = service.bar_delta(
        episode_index=0, episode_time=1.0, duration=8.0, preset_elapsed=1.0, wall=0.05
    )
    assert opening is not None and opening < 1.0
    service.follower._beats = 10000.0
    service.follower._at = service._now
    jump = service.bar_delta(
        episode_index=0, episode_time=1.0, duration=8.0, preset_elapsed=1.0, wall=0.05
    )
    assert jump is not None and jump >= 7.0
