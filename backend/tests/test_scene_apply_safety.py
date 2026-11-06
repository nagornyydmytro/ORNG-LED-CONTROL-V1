"""Raspberry auto-apply must not interrupt a live show."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orng_led.api.runtime import AppRuntime
from orng_led.cloud.metadata import CloudMetadata, save_cloud_metadata
from orng_led.cloud.scene_bundle import pack_scene_bundle
from orng_led.cloud.sync_service import SceneSyncService
from orng_led.config import default_scene_dir
from orng_led.config.models import ConfigError
from orng_led.main import create_app
from orng_led.cloud.scene_bundle import apply_release_safely


class _Response:
    def __init__(self, content: bytes, headers: dict[str, str]) -> None:
        self.status_code = 200
        self.content = content
        self.headers = headers
        self.text = ""


class _Client:
    def __init__(self, content: bytes, headers: dict[str, str]) -> None:
        self._content = content
        self._headers = headers

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _Response:
        return _Response(self._content, self._headers)


@pytest.fixture
def isolated_config(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(default_scene_dir(), target)
    return target


def _bundle(scene_dir: Path, scene_id: str) -> tuple[bytes, str]:
    raw, manifest = pack_scene_bundle(scene_dir)
    text = (scene_dir / "app.yaml").read_text(encoding="utf-8")
    (scene_dir / "app.yaml").write_text(text + "\n# remote\n", encoding="utf-8")
    remote, remote_manifest = pack_scene_bundle(scene_dir)
    (scene_dir / "app.yaml").write_text(text, encoding="utf-8")
    assert remote_manifest["content_sha256"] != manifest["content_sha256"]
    return remote, str(remote_manifest["content_sha256"])


def test_raspberry_auto_apply_does_not_change_live_show(
    isolated_config: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_MODE", "raspberry")
    monkeypatch.setenv("ORNG_OUTPUT_POLICY", "manual")
    monkeypatch.setenv("ORNG_CLOUD_METADATA_FILE", str(tmp_path / "cloud-metadata.json"))
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    runtime.engine.set_blackout(False)
    runtime.output.armed = True
    preset = runtime.engine.active_preset_id
    transport = runtime.output.status().transport
    remote, remote_hash = _bundle(isolated_config, runtime.scene_id)
    meta = CloudMetadata()
    save_cloud_metadata(meta)
    service = SceneSyncService(runtime)

    async def _run():
        return await service._sync_locked(
        client=_Client(remote, {"x-content-hash": remote_hash}),
        headers={},
        http_base="https://example.invalid",
        meta=meta,
        target_scene=runtime.scene_id,
        target_version=2,
        force=False,
        auto_apply=True,
        prefer_apply=False,
        command_id="test-live",
        )

    result = asyncio.run(_run())
    assert result["status"] == "deferred"
    assert result["reason"] == "show_active"
    assert runtime.engine.overlays.blackout is False
    assert runtime.output.armed is True
    assert runtime.engine.active_preset_id == preset
    assert runtime.output.status().transport == transport
    assert not (Path(runtime.config_dir) / "app.yaml").read_text(encoding="utf-8").endswith("# remote\n")


def test_apply_refuses_when_blackout_clears(
    isolated_config: Path, tmp_path: Path
) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    runtime.engine.set_blackout(False)
    preset = runtime.engine.active_preset_id
    raw, _manifest = pack_scene_bundle(isolated_config)
    from orng_led.cloud.scene_bundle import unpack_scene_bundle

    files, _hash = unpack_scene_bundle(raw)
    with pytest.raises(ConfigError, match="show_active"):
        apply_release_safely(
            staging_dir=tmp_path / "staging",
            final_dir=tmp_path / "final",
            files=files,
            runtime=runtime,
        )
    assert runtime.engine.overlays.blackout is False
    assert runtime.engine.active_preset_id == preset
    assert runtime.output.armed is False or runtime.output.armed is True


def test_publish_requires_draft_base(isolated_config: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    app = create_app(runtime=runtime, autostart_loop=False)
    with TestClient(app) as client:
        response = client.post("/api/cloud/publish", json={"scene_id": runtime.scene_id})
    assert response.status_code == 409
    assert "base_version_required" in response.text


def test_draft_patch_does_not_change_engine(isolated_config: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    app = create_app(runtime=runtime, autostart_loop=False)
    patch = runtime.show.patch.model_dump(mode="json")
    patch["fixtures"][0]["label"] = "DRAFT LABEL"
    with TestClient(app) as client:
        preset = runtime.engine.active_preset_id
        armed = runtime.output.armed
        blackout = runtime.engine.overlays.blackout
        saved = client.put(
            "/api/scene-draft/patch",
            json={"patch": patch, "base_version": 1},
        )
        assert saved.status_code == 200, saved.text
        again = client.get("/api/scene-draft/patch")
    assert again.json()["fixtures"][0]["label"] == "DRAFT LABEL"
    assert runtime.engine.active_preset_id == preset
    assert runtime.engine.overlays.blackout is blackout
    assert runtime.output.armed == armed
    assert runtime.show.patch.fixtures[0].label != "DRAFT LABEL"
    live = (isolated_config / "patch.yaml").read_text(encoding="utf-8")
    assert "DRAFT LABEL" not in live


def test_late_calibration_set_does_not_revive_session(isolated_config: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    runtime.begin_beam_calibration_test("beam_left", confirmed=True, owner="cloud:web:a")
    session_id = runtime.beam_calibration.session_id
    with pytest.raises(ValueError, match="іншій"):
        runtime.set_beam_calibration_position(pan=0.2, session_id=session_id, owner="cloud:web:b")
    runtime.end_beam_calibration_test()
    with pytest.raises(ValueError, match="не активне"):
        runtime.set_beam_calibration_position(pan=0.2, session_id=session_id, owner="cloud:web:a")
    assert runtime.beam_calibration.active is False


def test_scene_apply_blocks_venue_rearm(isolated_config: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=isolated_config)
    runtime.engine.set_blackout(False)
    runtime.scene_apply_in_progress = True
    status = runtime._venue_status_obj()
    assert runtime._venue_ready_attempt(status) is False
    assert runtime.engine.overlays.blackout is False


def test_flow_yaml_overlap_matches_block_rules() -> None:
    from orng_led.scenes.semantics import validate_scene_texts

    flow = {
        "scene.yaml": "scene_id: t\nname: t\n",
        "app.yaml": "schema_version: 1\n",
        "patch.yaml": 'fixtures: [{id: a, profile_id: p, start_address: "1"}, {id: b, profile_id: p, start_address: 1}]\n',
        "layout.yaml": "fixtures: []\n",
        "profiles/p.yaml": "footprint: 1\nchannels: []\n",
    }
    block = {
        **flow,
        "patch.yaml": "fixtures:\n- id: a\n  profile_id: p\n  start_address: 1\n- id: b\n  profile_id: p\n  start_address: 1\n",
    }
    flow_errors = validate_scene_texts(flow)
    block_errors = validate_scene_texts(block)
    assert any("цілим" in item or "перетина" in item for item in flow_errors)
    assert any("перетина" in item for item in block_errors)
