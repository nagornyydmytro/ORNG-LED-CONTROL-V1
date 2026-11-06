"""Cloud metadata classification + agent sync ACK contract."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orng_led.cloud.agent import CloudDeviceAgent, map_command_to_action
from orng_led.cloud.metadata import classify_sync_state, load_cloud_metadata, save_cloud_metadata
from orng_led.cloud.scene_bundle import unpack_scene_bundle
from orng_led.cloud.sync_service import SceneSyncError
from orng_led.config.models import ConfigError


def test_classify_dirty_update_conflict() -> None:
    assert (
        classify_sync_state(
            local_hash="aaa",
            last_synced_hash="aaa",
            remote_hash="bbb",
        )
        == "update_available"
    )
    assert (
        classify_sync_state(
            local_hash="ccc",
            last_synced_hash="aaa",
            remote_hash="aaa",
        )
        == "dirty"
    )
    assert (
        classify_sync_state(
            local_hash="ccc",
            last_synced_hash="aaa",
            remote_hash="bbb",
        )
        == "conflict"
    )
    assert (
        classify_sync_state(
            local_hash="aaa",
            last_synced_hash="aaa",
            remote_hash="aaa",
        )
        == "synced"
    )


def test_early_synced_persists_applied_and_last_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When local already matches remote, metadata still records applied/last_synced."""
    import asyncio
    from unittest.mock import MagicMock

    from orng_led.cloud.sync_service import SceneSyncService

    monkeypatch.setenv("ORNG_CLOUD_METADATA_FILE", str(tmp_path / "meta.json"))
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_URL", "https://device.example.test")
    monkeypatch.setenv("ORNG_CLOUD_TOKEN_FILE", str(tmp_path / "tok"))
    (tmp_path / "tok").write_text("device-token", encoding="utf-8")

    runtime = MagicMock()
    runtime.config_root = tmp_path
    runtime.config_dir = tmp_path / "scenes" / "orng-bar"
    runtime.scene_id = "orng-bar"
    runtime.app_version = "0.2.0"
    (runtime.config_dir).mkdir(parents=True)
    (runtime.config_dir / "scene.yaml").write_text("id: orng-bar\n", encoding="utf-8")

    svc = SceneSyncService(runtime)
    matched = "aa" * 32

    class _Resp:
        status_code = 200
        headers = {"x-content-hash": matched}
        content = b"{}"
        text = ""

        def json(self):
            return {
                "assignment": {
                    "scene_id": "orng-bar",
                    "desired_version": 1,
                }
            }

    class _Client:
        async def get(self, url, headers=None):  # noqa: ARG002
            return _Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):  # noqa: ARG002
            return False

    monkeypatch.setattr("httpx.AsyncClient", lambda **kw: _Client())  # noqa: ARG005
    monkeypatch.setattr(
        svc,
        "_local_hash_for_scene",
        lambda scene_id: matched,  # noqa: ARG005
    )

    result = asyncio.run(svc.sync_assigned_release())
    assert result["status"] == "synced"
    meta = load_cloud_metadata()
    scene = meta.scene("orng-bar")
    assert scene.applied_version == 1
    assert scene.applied_hash == matched
    assert scene.last_synced_version == 1
    assert scene.last_synced_hash == matched
    assert scene.sync_status == "synced"


def test_metadata_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORNG_CLOUD_METADATA_FILE", str(tmp_path / "meta.json"))
    meta = load_cloud_metadata()
    scene = meta.scene("orng-bar")
    scene.last_synced_hash = "abc"
    scene.applied_version = 2
    save_cloud_metadata(meta)
    again = load_cloud_metadata()
    assert again.scene("orng-bar").last_synced_hash == "abc"
    assert again.scene("orng-bar").applied_version == 2


def test_unpack_rejects_duplicate_and_bad_b64() -> None:
    good = {
        "format": "orng-scene-bundle-v1",
        "files": [
            {
                "path": "app.yaml",
                "sha256": "00" * 32,
                "data_b64": base64.b64encode(b"x").decode(),
            },
            {
                "path": "app.yaml",
                "sha256": "00" * 32,
                "data_b64": base64.b64encode(b"x").decode(),
            },
        ],
    }
    # Fix hash for first entry so we hit duplicate path
    data = b"x"
    digest = __import__("hashlib").sha256(data).hexdigest()
    good["files"][0]["sha256"] = digest
    good["files"][1]["sha256"] = digest
    with pytest.raises(ConfigError, match="Duplicate"):
        unpack_scene_bundle(json.dumps(good).encode())

    bad = {
        "format": "orng-scene-bundle-v1",
        "files": [
            {"path": "app.yaml", "sha256": "00", "data_b64": "!!!not-b64!!!"},
        ],
    }
    with pytest.raises(ConfigError, match="base64"):
        unpack_scene_bundle(json.dumps(bad).encode())


def test_agent_sync_acks_only_after_apply() -> None:
    import asyncio

    runtime = MagicMock()
    runtime.config_root = Path(".")
    runtime.build_state = MagicMock()
    agent = CloudDeviceAgent(runtime)
    ws = AsyncMock()
    msg = {
        "type": "sync_scene_release",
        "message_id": "m1",
        "device_id": "inst-1",
        "payload": {"command_id": "c1", "scene_id": "orng-bar", "version": 2},
    }

    async def _run() -> None:
        with patch.object(
            agent._sync,
            "sync_assigned_release",
            AsyncMock(return_value={"status": "applied", "scene_id": "orng-bar", "version": 2}),
        ):
            await agent._handle_message(ws, msg)
        sent = json.loads(ws.send.await_args.args[0])
        assert sent["payload"]["status"] == "applied"

        with patch.object(
            agent._sync,
            "sync_assigned_release",
            AsyncMock(side_effect=SceneSyncError("bundle_invalid", "corrupt")),
        ):
            await agent._handle_message(
                ws, {**msg, "payload": {"command_id": "c2", "version": 3}}
            )
        sent_fail = json.loads(ws.send.await_args.args[0])
        assert sent_fail["payload"]["status"] == "failed"
        assert sent_fail["payload"]["error_code"] == "bundle_invalid"

    asyncio.run(_run())


def test_agent_never_puts_token_in_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_URL", "wss://example.test/ws/device")
    monkeypatch.setenv("ORNG_CLOUD_TOKEN_FILE", str(Path("/tmp/should-not-leak.token")))
    runtime = MagicMock()
    runtime.config_root = Path(".")
    agent = CloudDeviceAgent(runtime)
    status = agent.status
    blob = json.dumps(status)
    assert "token=" not in blob
    assert "wss://example.test" not in blob or "url_configured" in status
    assert status.get("url_configured") is True


def test_sync_scene_release_mapped() -> None:
    assert map_command_to_action("sync_scene_release") == "sync_scene_release"
