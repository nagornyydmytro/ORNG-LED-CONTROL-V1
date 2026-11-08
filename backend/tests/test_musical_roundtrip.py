"""Musical preset through a temp scene. Nothing is published and Art-Net stays off."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import yaml

from orng_led.api.runtime import AppRuntime
from orng_led.output.mock import MockTransport
from orng_led.cloud.scene_bundle import pack_scene_bundle, unpack_scene_bundle
from orng_led.config import default_scene_dir
from orng_led.config.io import load_show_config_from_scene_dir
from orng_led.presets.io import load_preset
from orng_led.presets.models import (
    MUSICAL_CYCLE_VERSION,
    EpisodeCard,
    LookAction,
    PresetDocument,
)


def _scene(root: Path) -> Path:
    dest = root / "scenes" / "test-musical"
    shutil.copytree(default_scene_dir(), dest)
    (dest / "scene.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": 1, "scene_id": "test-musical", "name": "Test musical"},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    app = yaml.safe_load((dest / "app.yaml").read_text(encoding="utf-8"))
    app["transport"] = "mock"
    app["output_armed"] = False
    (dest / "app.yaml").write_text(yaml.safe_dump(app, sort_keys=False), encoding="utf-8")
    return dest


def _document() -> PresetDocument:
    return PresetDocument(
        id="M01",
        label="Музичний",
        builtin=False,
        musical_version=MUSICAL_CYCLE_VERSION,
        episodes=[
            EpisodeCard(
                id="short",
                duration_s=4,
                groups=["par"],
                palette="warm_orange",
                effect="pulse",
                speed=0.5,
                intensity=0.8,
                transition="cut",
                actions=[
                    LookAction(id="a1", groups=["par"], effect="pulse", speed=0.5, intensity=0.8, cycle_beats=1),
                    LookAction(id="a2", groups=["bar"], effect="chase", speed=0.5, intensity=0.7, cycle_beats=2),
                    LookAction(id="a4", groups=["left", "par"], effect="wave", speed=0.4, intensity=0.6, cycle_beats=4),
                    LookAction(id="a8", groups=["beam"], effect="breathe", speed=0.3, intensity=0.5, cycle_beats=8),
                ],
            ),
            EpisodeCard(
                id="long",
                duration_s=11,
                groups=["bar"],
                palette="cool_blue",
                effect="wave",
                speed=0.4,
                intensity=0.6,
                transition="soft",
                cycle_beats=4,
            ),
        ],
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_musical_preset_survives_draft_publish_load_and_apply(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    patch_hash = _sha(scene / "patch.yaml")
    profile_hash = {
        path.name: _sha(path) for path in sorted((scene / "profiles").glob("*.yaml"))
    }
    assert "beam_13ch.yaml" in profile_hash
    original = _document()
    runtime = AppRuntime.create(
        autostart_loop=False,
        show=load_show_config_from_scene_dir(scene),
        config_dir=scene,
    )
    assert isinstance(runtime.output.transport, MockTransport)
    assert runtime.output.armed is False
    runtime.create_preset(original.model_dump(mode="json"))
    edited = original.model_copy(deep=True)
    edited.episodes[1].duration_s = 9
    edited.episodes[0].actions[2].cycle_beats = 8
    runtime.update_preset("M01", edited.model_dump(mode="json"))
    draft = load_preset(scene / "presets" / "M01.yaml")
    assert draft.model_dump(mode="json") == runtime.get_preset_document("M01")
    assert [action.cycle_beats for action in draft.episodes[0].actions] == [1, 2, 8, 8]
    assert [episode.duration_s for episode in draft.episodes] == [4, 9]

    raw, manifest = pack_scene_bundle(scene)
    again, content_hash = unpack_scene_bundle(raw)
    assert content_hash == manifest["content_sha256"]
    packed = again["presets/M01.yaml"]
    assert hashlib.sha256(packed).hexdigest() == _sha(scene / "presets" / "M01.yaml")
    assert hashlib.sha256(again["patch.yaml"]).hexdigest() == patch_hash

    desktop = tmp_path / "desktop"
    desktop.mkdir()
    for path, data in again.items():
        target = desktop / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    loaded = load_preset(desktop / "presets" / "M01.yaml")
    assert loaded.model_dump(mode="json") == draft.model_dump(mode="json")

    player = AppRuntime.create(
        autostart_loop=False,
        show=load_show_config_from_scene_dir(desktop),
        config_dir=desktop,
    )
    player.engine.set_blackout(False)
    player.apply_select_preset("M01")
    program = player.engine.active_preset
    assert program.primary_cycle_beats(0.0) == 1
    assert program.cycle_position(0.0).episode_index == 0
    assert program.cycle_position(4.0).episode_index == 1
    assert program.primary_cycle_beats(4.0) == 4
    frame = player.engine.frame_at_preset_time(0.5)
    assert any(channel > 0 for channel in frame)

    player.update_preset("M01", loaded.model_dump(mode="json"))
    saved = load_preset(desktop / "presets" / "M01.yaml")
    assert saved.musical_version == MUSICAL_CYCLE_VERSION
    assert saved.model_dump(mode="json") == loaded.model_dump(mode="json")
    repacked, second = pack_scene_bundle(desktop)
    assert second["content_sha256"] == hashlib.sha256(repacked).hexdigest()
    unpacked_profiles = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((desktop / "profiles").glob("*.yaml"))
    }
    assert unpacked_profiles == profile_hash
    assert _sha(desktop / "patch.yaml") == patch_hash
