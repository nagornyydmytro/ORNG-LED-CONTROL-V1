"""Two scene documents can share fixture names and still stay apart."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

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
from orng_led.presets.io import load_presets_dir, save_preset
from orng_led.presets.models import EpisodeCard, PresetDocument
from orng_led.scenes.bootstrap_show import EMPTY_SCENE_ID, empty_safe_manifest, empty_safe_show
from orng_led.scenes.drafts import draft_dir, load_overlay_preset, mark_deleted, write_draft_preset
from orng_led.scenes.models import SceneManifest


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_scene(
    root: Path,
    *,
    scene_id: str,
    address: int,
    pan: float,
    label: str,
    intensity: float,
) -> None:
    root.mkdir(parents=True)
    save_model(
        root / "scene.yaml",
        SceneManifest(scene_id=scene_id, name=scene_id),  # type: ignore[arg-type]
    )
    save_model(root / "app.yaml", AppConfig())
    save_model(
        root / "profiles" / "profile_1.yaml",
        FixtureProfile(
            id="profile_1",
            label="profile_1",
            kind=FixtureKind.PAR,
            footprint=1,
            channels=[ChannelDefinition(local=1, role=ChannelRole.DIMMER)],
        ),
    )
    save_model(
        root / "patch.yaml",
        PatchDocument(
            fixtures=[
                FixtureInstance(
                    id="par_1",
                    profile_id="profile_1",
                    label="par_1",
                    start_address=address,
                    kind=FixtureKind.PAR,
                    spatial=SpatialPlacement(side=Side.LEFT, pan_offset=pan),
                )
            ]
        ),
    )
    save_model(
        root / "layout.yaml",
        SpatialLayout(
            fixtures=["par_1"],
            placements=[
                StagePlacement(fixture_id="par_1", kind=FixtureKind.PAR, x=0.2, y=0.4)
            ],
        ),
    )
    save_preset(
        root / "presets" / "P01.yaml",
        PresetDocument(
            id="P01",
            label=label,
            episodes=[EpisodeCard(id="ep", duration_s=4, intensity=intensity)],
        ),
    )


def test_same_names_do_not_cross_scenes_or_drafts(tmp_path: Path) -> None:
    scene_a = tmp_path / "scenes" / "scene-a"
    scene_b = tmp_path / "scenes" / "scene-b"
    _write_scene(scene_a, scene_id="scene-a", address=20, pan=0.2, label="A-red", intensity=0.2)
    _write_scene(scene_b, scene_id="scene-b", address=100, pan=-0.2, label="B-blue", intensity=0.9)
    hash_b = _tree_hash(scene_b)

    loaded_a = load_show_config_from_scene_dir(scene_a)
    loaded_b = load_show_config_from_scene_dir(scene_b)
    assert loaded_a.patch.fixture("par_1").start_address == 20
    assert loaded_a.patch.fixture("par_1").spatial.pan_offset == pytest.approx(0.2)
    assert loaded_b.patch.fixture("par_1").start_address == 100
    assert loaded_b.patch.fixture("par_1").spatial.pan_offset == pytest.approx(-0.2)
    assert loaded_a.patch.fixture("par_1").profile_id == "profile_1"
    assert load_presets_dir(scene_a / "presets")["P01"].label == "A-red"
    assert load_presets_dir(scene_b / "presets")["P01"].label == "B-blue"
    assert "orng-bar" not in (scene_a / "app.yaml").read_text(encoding="utf-8")
    assert "token" not in (scene_a / "app.yaml").read_text(encoding="utf-8")
    assert loaded_a.app.artnet.target_ip is None

    save_model(
        scene_b / "profiles" / "unused.yaml",
        FixtureProfile(
            id="unused",
            label="unused",
            kind=FixtureKind.PAR,
            footprint=1,
            channels=[ChannelDefinition(local=1, role=ChannelRole.DIMMER)],
        ),
    )
    (scene_b / "profiles" / "profile_1.yaml").unlink()
    with pytest.raises(ConfigError, match="profile_1") as missing:
        load_show_config_from_scene_dir(scene_b)
    assert "scene-a" not in str(missing.value)
    assert load_presets_dir(scene_a / "presets")["P01"].label == "A-red"
    assert (scene_a / "profiles" / "profile_1.yaml").is_file()

    other_computer = tmp_path / "other-computer"
    _write_scene(
        other_computer / "scenes" / "scene-a",
        scene_id="scene-a",
        address=40,
        pan=0.0,
        label="other-computer",
        intensity=0.5,
    )
    other_hash = _tree_hash(other_computer)
    doc_a = PresetDocument(
        id="P01",
        label="draft-A",
        episodes=[EpisodeCard(id="ep", duration_s=4, intensity=0.3)],
    )
    doc_b = PresetDocument(
        id="P01",
        label="draft-B",
        episodes=[EpisodeCard(id="ep", duration_s=4, intensity=0.8)],
    )
    write_draft_preset(tmp_path, "scene-a", doc_a, base_version=1)
    write_draft_preset(tmp_path, "scene-b", doc_b, base_version=1)
    assert load_overlay_preset(scene_a, tmp_path, "scene-a", "P01").label == "draft-A"
    assert load_overlay_preset(scene_b, tmp_path, "scene-b", "P01").label == "draft-B"
    mark_deleted(tmp_path, "scene-a", "presets/P01.yaml", base_version=1)
    assert not (draft_dir(tmp_path, "scene-a") / "presets" / "P01.yaml").is_file()
    assert load_overlay_preset(scene_b, tmp_path, "scene-b", "P01").label == "draft-B"
    assert _tree_hash(other_computer) == other_hash
    assert (other_computer / "scenes" / "scene-a" / "presets" / "P01.yaml").read_text(
        encoding="utf-8"
    ).find("other-computer") >= 0
    assert "draft-A" not in (scene_b / "presets" / "P01.yaml").read_text(encoding="utf-8")
    assert hash_b != _tree_hash(scene_a)


def test_empty_bootstrap_is_not_an_orng_bar_copy() -> None:
    show = empty_safe_show()
    manifest = empty_safe_manifest()
    assert manifest.scene_id == EMPTY_SCENE_ID
    assert manifest.scene_id != "orng-bar"
    assert show.patch.fixtures == []
    assert show.app.output_armed is False
    assert show.app.artnet.target_ip is None
    blob = show.app.model_dump_json() + manifest.model_dump_json()
    assert "orng-bar" not in blob
    assert "token" not in blob
    assert "executor_installation_id" not in blob
