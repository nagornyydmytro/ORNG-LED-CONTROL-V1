"""apply_release_safely must confirm restore. Two scenes, same fixture names."""

from __future__ import annotations

from pathlib import Path

import pytest

from orng_led.api.runtime import AppRuntime
from orng_led.cloud.scene_bundle import (
    ApplyReleaseError,
    apply_release_safely,
    collect_scene_files,
    is_scene_tree,
    recover_interrupted_swap,
    scene_backup_dir,
    scene_journal_path,
    scene_revision,
    write_bundle_to_dir,
)
from orng_led.config.io import save_model
from orng_led.config.models import (
    DEFAULT_PAD_PRESETS,
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
from orng_led.input.contract import InputAction, InputEvent, InputSource
from orng_led.presets.io import save_preset
from orng_led.presets.models import EpisodeCard, PresetDocument
from orng_led.scenes.bootstrap_show import EMPTY_SCENE_ID
from orng_led.scenes.store import SceneStore


def _write_scene(root: Path, *, scene_id: str, address: int) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "scene.yaml").write_text(
        f"schema_version: 1\nscene_id: {scene_id}\nname: {scene_id}\n",
        encoding="utf-8",
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
                    groups=["par"],
                    spatial=SpatialPlacement(side=Side.LEFT, pan_offset=0.1),
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
    for preset_id in DEFAULT_PAD_PRESETS:
        save_preset(
            root / "presets" / f"{preset_id}.yaml",
            PresetDocument(
                id=preset_id,
                label=f"addr-{address}-{preset_id}",
                episodes=[EpisodeCard(id="ep", duration_s=4, intensity=0.5)],
            ),
        )
    return root


def _config(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "config"
    live = _write_scene(root / "scenes" / "scene-a", scene_id="scene-a", address=20)
    other = _write_scene(root / "scenes" / "scene-b", scene_id="scene-b", address=100)
    (root / "runtime.yaml").write_text(
        "schema_version: 1\nactive_scene_id: scene-a\n",
        encoding="utf-8",
    )
    return root, live, other


def _release_files(other: Path, *, scene_id: str) -> dict[str, bytes]:
    packed = other.parent / f"pack-{scene_id}"
    if packed.exists():
        raise AssertionError(packed)
    _write_scene(packed, scene_id=scene_id, address=100)
    return collect_scene_files(packed)


def _runtime(root: Path) -> AppRuntime:
    return AppRuntime.create(autostart_loop=False, config_dir=root)


def _address(runtime: AppRuntime) -> int:
    return int(runtime.show.patch.fixture("par_1").start_address)


def _apply(
    runtime: AppRuntime,
    live: Path,
    files: dict[str, bytes],
    *,
    fault=None,
) -> object:
    return apply_release_safely(
        staging_dir=live.parent / ".staging-scene-a",
        final_dir=live,
        files=files,
        runtime=runtime,
        fault=fault,
    )


def test_second_rename_restores_and_retry_keeps_the_working_copy(tmp_path: Path) -> None:
    root, live, other = _config(tmp_path)
    files = _release_files(other, scene_id="scene-a")
    runtime = _runtime(root)
    runtime.engine.set_blackout(True)
    assert _address(runtime) == 20

    def fail_after_backup(phase: str) -> None:
        if phase == "after_backup":
            raise OSError("injected staging rename")

    with pytest.raises(ApplyReleaseError, match="rolled_back") as raised:
        _apply(runtime, live, files, fault=fail_after_backup)
    outcome = raised.value.outcome
    assert raised.value.code == "rolled_back"
    assert "rolled back" not in str(raised.value).lower() or "rolled_back" in str(raised.value)
    assert outcome.status == "rolled_back"
    assert outcome.files_in_place is True
    assert outcome.scene_loaded is True
    assert is_scene_tree(live)
    assert scene_revision(live) != scene_revision(other.parent / "pack-scene-a")
    assert _address(runtime) == 20
    assert runtime.engine.overlays.blackout is True
    assert runtime.output.armed is False

    again = _runtime(root)
    assert again.scene_id == "scene-a"
    assert _address(again) == 20
    assert again.scene_error is None

    again.engine.set_blackout(True)
    applied = _apply(again, live, files)
    assert applied.status == "applied"
    assert applied.files_in_place is True
    assert applied.scene_loaded is True
    assert applied.restart_revision == scene_revision(live)
    assert _address(again) == 100

    restarted = _runtime(root)
    assert _address(restarted) == 100
    assert restarted.scene_id == "scene-a"


def test_process_interrupt_between_renames_recovers_on_new_runtime(tmp_path: Path) -> None:
    root, live, other = _config(tmp_path)
    files = _release_files(other, scene_id="scene-a")
    staging = live.parent / ".staging-scene-a"
    write_bundle_to_dir(files, staging)
    backup = scene_backup_dir(live)
    live.rename(backup)
    scene_journal_path(live).write_text("swapping", encoding="utf-8")
    assert not is_scene_tree(live)
    assert is_scene_tree(backup)

    recovered = _runtime(root)
    assert recovered.scene_id == "scene-a"
    assert _address(recovered) == 20
    assert is_scene_tree(live)
    assert not is_scene_tree(backup)
    assert recover_interrupted_swap(live) == "final"


def test_retry_does_not_delete_the_only_working_copy(tmp_path: Path) -> None:
    root, live, other = _config(tmp_path)
    files = _release_files(other, scene_id="scene-a")
    backup = scene_backup_dir(live)
    live.rename(backup)
    runtime = _runtime(root)
    runtime.engine.set_blackout(True)
    assert _address(runtime) == 20
    applied = _apply(runtime, live, files)
    assert applied.status == "applied"
    assert _address(runtime) == 100
    assert is_scene_tree(live)
    restarted = _runtime(root)
    assert _address(restarted) == 100


def test_activate_failure_after_rename_is_not_applied(tmp_path: Path) -> None:
    root, live, other = _config(tmp_path)
    files = _release_files(other, scene_id="scene-a")
    runtime = _runtime(root)
    runtime.engine.set_blackout(True)
    runtime.input_dispatcher.dispatch(
        InputEvent(
            action=InputAction.STROBE_PRESS,
            source=InputSource.UI,
            source_id="cloud:web:a",
        )
    )
    assert runtime.engine.overlays.strobe_held is True

    def boom(scene_id: str, **_kwargs: object) -> tuple[object, bool]:
        raise ConfigError("injected activate")

    runtime.activate_scene = boom  # type: ignore[method-assign]
    with pytest.raises(ApplyReleaseError) as raised:
        _apply(runtime, live, files)
    assert raised.value.code == "activate_failed"
    assert "rolled_back" not in raised.value.code
    assert "applied" not in raised.value.outcome.status
    assert raised.value.outcome.files_in_place is True
    assert raised.value.outcome.scene_loaded is False
    assert scene_revision(live) == scene_revision(other.parent / "pack-scene-a")
    assert _address(runtime) == 20
    assert runtime.engine.overlays.blackout is True
    assert runtime.output.armed is False
    assert is_scene_tree(scene_backup_dir(live)) or is_scene_tree(
        live.with_name(live.name + ".previous-good")
    )

    restarted = _runtime(root)
    assert _address(restarted) == 100
    assert restarted.engine.overlays.strobe_held is False
    assert restarted.engine.overlays.blackout is True


def test_pointer_write_failure_is_not_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, live, other = _config(tmp_path)
    files = _release_files(other, scene_id="scene-a")
    runtime = _runtime(root)
    runtime.engine.set_blackout(True)

    def boom(self: SceneStore, scene_id: str) -> object:
        raise ConfigError("injected pointer")

    monkeypatch.setattr(SceneStore, "save_active_scene_id", boom)
    with pytest.raises(ApplyReleaseError) as raised:
        _apply(runtime, live, files)
    assert raised.value.code == "pointer_unconfirmed"
    assert raised.value.outcome.status == "pointer_unconfirmed"
    assert raised.value.outcome.files_in_place is True
    assert raised.value.outcome.scene_loaded is True
    assert _address(runtime) == 100
    assert runtime.engine.overlays.blackout is True
    text = (root / "runtime.yaml").read_text(encoding="utf-8")
    assert "scene-a" in text

    restarted = _runtime(root)
    assert _address(restarted) == 100


def test_successful_apply_drops_old_holds_and_patch(tmp_path: Path) -> None:
    root, live, other = _config(tmp_path)
    files = _release_files(other, scene_id="scene-a")
    runtime = _runtime(root)
    runtime.engine.set_blackout(False)
    runtime.input_dispatcher.dispatch(
        InputEvent(
            action=InputAction.STROBE_PRESS,
            source=InputSource.UI,
            source_id="cloud:web:a",
        )
    )
    assert runtime.engine.overlays.strobe_held is True
    runtime.engine.set_blackout(True)
    applied = _apply(runtime, live, files)
    assert applied.status == "applied"
    assert _address(runtime) == 100
    assert runtime.engine.overlays.strobe_held is False
    assert runtime.engine.overlays.blackout is True
    dark = runtime.tick(0.05).frame
    assert dark[19] == 0
    assert dark[99] == 0
    restarted = _runtime(root)
    assert _address(restarted) == 100
    assert restarted.engine.overlays.strobe_held is False


def test_restore_impossible_stays_safe_and_keeps_remaining_copy(tmp_path: Path) -> None:
    root, live, other = _config(tmp_path)
    files = _release_files(other, scene_id="scene-a")
    runtime = _runtime(root)
    runtime.engine.set_blackout(True)
    backup = scene_backup_dir(live)

    def wipe_and_fail(phase: str) -> None:
        if phase == "after_backup":
            if backup.exists():
                import shutil

                shutil.rmtree(backup)
            raise OSError("injected staging rename")

    with pytest.raises(ApplyReleaseError) as raised:
        _apply(runtime, live, files, fault=wipe_and_fail)
    assert raised.value.code == "restore_failed"
    assert "rolled_back" not in raised.value.code
    assert "applied" not in raised.value.outcome.status
    assert runtime.engine.overlays.blackout is True
    assert runtime.output.armed is False
    staging = live.parent / ".staging-scene-a"
    assert is_scene_tree(staging) or is_scene_tree(live)

    restarted = _runtime(root)
    assert restarted.engine.overlays.blackout is True
    assert restarted.output.armed is False
    if restarted.scene_id == EMPTY_SCENE_ID:
        assert restarted.scene_error
        assert restarted.show.patch.fixtures == []
    else:
        assert _address(restarted) == 20
