"""Draft edits stay off the live engine until a publish that still matches."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.audio.service import BpmService
from orng_led.config import default_scene_dir
from orng_led.main import create_app
from orng_led.scenes.drafts import discard_if_unchanged, draft_fingerprint, draft_lock


@pytest.fixture
def isolated_config(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(default_scene_dir(), target)
    return target


def test_delete_rename_and_profiles_stay_in_the_draft(isolated_config: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    live_before = sorted(path.name for path in (isolated_config / "presets").glob("*.yaml"))
    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app) as client:
        listed = client.get("/api/scene-draft/presets")
        assert listed.status_code == 200
        ids = {item["id"] for item in listed.json()["presets"]}
        assert "P01" in ids
        created = client.post(
            "/api/scene-draft/presets",
            json={"id": "Draft1", "label": "Чернетка", "base_version": 1},
        )
        assert created.status_code == 200
        renamed = client.post(
            "/api/scene-draft/presets/Draft1/rename",
            json={"label": "Нова", "base_version": 1},
        )
        assert renamed.status_code == 200
        fetched = client.get("/api/scene-draft/presets/Draft1")
        assert fetched.json()["label"] == "Нова"
        deleted = client.request(
            "DELETE",
            "/api/scene-draft/presets/P01",
            json={"base_version": 1},
        )
        assert deleted.status_code == 200
        again = {item["id"] for item in client.get("/api/scene-draft/presets").json()["presets"]}
        assert "P01" not in again
        assert "Draft1" in again
        profiles = client.get("/api/scene-draft/profiles")
        assert profiles.status_code == 200
        assert profiles.json()
    live_after = sorted(path.name for path in (isolated_config / "presets").glob("*.yaml"))
    assert live_after == live_before
    assert (isolated_config / "presets" / "P01.yaml").is_file()
    assert runtime.engine.active_preset_id


def test_a_save_during_publish_is_not_deleted(isolated_config: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    scene_id = runtime.scene_id
    with draft_lock(scene_id):
        before = draft_fingerprint(runtime.config_root, scene_id)
    client_app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(client_app) as client:
        saved = client.put(
            "/api/scene-draft/patch",
            json={"patch": runtime.show.patch.model_dump(mode="json"), "base_version": 3},
        )
        assert saved.status_code == 200
    assert discard_if_unchanged(runtime.config_root, scene_id, before) is True
    assert (runtime.config_root / "drafts" / scene_id / "patch.yaml").is_file()


def test_bpm_prefs_stay_with_one_scene_and_ignore_the_live_tempo(tmp_path: Path) -> None:
    bpm = BpmService(tmp_path, "scene-a")
    bpm.configure(enabled=True, mode="bar", scope="auto", palette="blue")
    stored = (tmp_path / "location-show" / "scene-a.json").read_text(encoding="utf-8")
    assert "blue" in stored
    assert "enabled" not in stored
    bpm.follower.bpm = 140
    bpm.configure(enabled=False)
    assert "140" not in (tmp_path / "location-show" / "scene-a.json").read_text(encoding="utf-8")
    bpm.set_scene("scene-b")
    assert bpm.palette == "orange"
    assert bpm.enabled is False
    assert not (tmp_path / "location-show" / "scene-b.json").exists()
    bpm.set_scene("scene-a")
    assert bpm.palette == "blue"
    assert bpm.mode == "bar"
    assert bpm.enabled is False
