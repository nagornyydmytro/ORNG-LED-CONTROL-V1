"""Hardening: scene_id, path escape, hash mismatch, pairing order, rotation, locks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orng_led.cloud.credentials import CredentialStorageError, save_device_token
from orng_led.cloud.ids import (
    assert_safe_scene_id,
    resolve_under_scenes_root,
    sanitize_staging_token,
)
from orng_led.cloud.rotation import complete_pending_rotation
from orng_led.cloud.scene_bundle import validate_unpacked_release
from orng_led.cloud.sync_service import SceneSyncService
from orng_led.config.models import ConfigError


def test_strict_scene_id() -> None:
    assert assert_safe_scene_id("orng-bar") == "orng-bar"
    with pytest.raises(ConfigError):
        assert_safe_scene_id("../etc")
    with pytest.raises(ConfigError):
        assert_safe_scene_id("ORNG")
    with pytest.raises(ConfigError):
        assert_safe_scene_id("a/b")
    with pytest.raises(ConfigError):
        assert_safe_scene_id("a\\b")


def test_sanitize_staging_token_portable() -> None:
    assert ":" not in sanitize_staging_token("cloud:web:abc")
    assert "\\" not in sanitize_staging_token("C:\\foo")
    assert ".." not in sanitize_staging_token("..\\..")
    assert sanitize_staging_token("ok-CMD_1.2") == "ok-CMD_1.2"


def test_resolve_under_scenes_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "scenes"
    root.mkdir()
    with pytest.raises(ConfigError):
        resolve_under_scenes_root(root, "..", "secret")
    ok = resolve_under_scenes_root(root, "orng-bar")
    assert ok.parent == root.resolve()


def test_content_hash_mismatch_rejected() -> None:
    files = {
        "scene.yaml": b"scene_id: orng-bar\n",
        "app.yaml": b"x: 1\n",
        "patch.yaml": b"x: 1\n",
        "layout.yaml": b"x: 1\n",
    }
    with pytest.raises(ConfigError, match="hash mismatch"):
        validate_unpacked_release(
            files,
            expected_scene_id="orng-bar",
            expected_content_hash="deadbeef",
            content_hash="cafebabe",
        )


def test_scene_id_mismatch_in_yaml() -> None:
    files = {
        "scene.yaml": b"scene_id: other-scene\n",
        "app.yaml": b"x: 1\n",
        "patch.yaml": b"x: 1\n",
        "layout.yaml": b"x: 1\n",
    }
    with pytest.raises(ConfigError, match="does not match"):
        validate_unpacked_release(
            files,
            expected_scene_id="orng-bar",
            content_hash="abc",
        )


def test_atomic_token_write_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORNG_CLOUD_TOKEN_FILE", str(tmp_path / "device.token"))
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_MODE", "desktop")
    monkeypatch.setenv("ORNG_CLOUD_ALLOW_FILE_TOKEN", "1")
    monkeypatch.setenv("ORNG_CLOUD_PREFER_KEYRING", "0")
    path = save_device_token("abc123token")
    assert path is not None
    assert path.read_text(encoding="utf-8").strip() == "abc123token"
    # On Windows mode bits are limited; ensure no leftover .tmp
    assert not path.with_name(path.name + ".tmp").exists()


def test_pairing_save_failure_does_not_consume(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit-level ordering: consume must not run if save raises."""
    calls: list[str] = []

    def fake_save(_token: str) -> None:
        calls.append("save")
        raise CredentialStorageError("disk full")

    async def fake_consume() -> None:
        calls.append("consume")

    async def flow() -> None:
        token = "tok"
        try:
            fake_save(token)
            await fake_consume()
        except CredentialStorageError:
            pass

    asyncio.run(flow())
    assert calls == ["save"]
    assert "consume" not in calls


def test_rotation_flow_retry_boundaries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ORNG_CLOUD_TOKEN_FILE", str(tmp_path / "device.token"))
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_MODE", "desktop")
    monkeypatch.setenv("ORNG_CLOUD_ALLOW_FILE_TOKEN", "1")
    monkeypatch.setenv("ORNG_CLOUD_PREFER_KEYRING", "0")
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_URL", "https://device.test/ws/device")
    monkeypatch.setenv("ORNG_CLOUD_ROTATION_KEY_FILE", str(tmp_path / "rot.pem"))
    save_device_token("old-token-value")

    class FakeResp:
        def __init__(self, status: int, payload: dict) -> None:
            self.status_code = status
            self._payload = payload

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self) -> None:
            self.posts = 0

        async def get(self, url: str, headers: dict | None = None) -> FakeResp:
            _ = url, headers
            return FakeResp(200, {"rotation_pending": False})

        async def post(self, *a: object, **k: object) -> FakeResp:
            self.posts += 1
            return FakeResp(200, {})

        async def aclose(self) -> None:
            return None

    async def _run() -> None:
        client = FakeClient()
        changed = await complete_pending_rotation(http_client=client)
        assert changed is False
        assert client.posts == 0

    asyncio.run(_run())


@pytest.mark.parametrize(
    "status",
    ["conflict", "dirty", "update_available"],
)
def test_agent_does_not_ack_applied_for_non_apply_status(status: str) -> None:
    from orng_led.cloud.agent import CloudDeviceAgent

    runtime = MagicMock()
    runtime.config_root = Path(".")
    agent = CloudDeviceAgent(runtime)
    ws = AsyncMock()

    async def _run() -> None:
        with patch.object(
            agent._sync,
            "sync_assigned_release",
            AsyncMock(return_value={"status": status}),
        ):
            await agent._handle_message(
                ws,
                {
                    "type": "sync_scene_release",
                    "message_id": "m1",
                    "payload": {"command_id": "c1", "scene_id": "orng-bar", "version": 2},
                },
            )
        sent = json.loads(ws.send.await_args.args[0])
        assert sent["payload"]["status"] == "failed"
        assert sent["payload"]["error_code"] == status

    asyncio.run(_run())


def test_per_scene_sync_lock_serializes(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = MagicMock()
    runtime.config_root = Path(".")
    runtime.scene_id = "orng-bar"
    runtime.config_dir = Path(".")
    svc = SceneSyncService(runtime)

    order: list[str] = []

    async def slow_locked(**kwargs: object) -> dict:
        order.append("enter")
        await asyncio.sleep(0.05)
        order.append("leave")
        return {"status": "no_assignment"}

    async def _run() -> None:
        with (
            patch("orng_led.cloud.sync_service.load_device_token", return_value="t"),
            patch("orng_led.cloud.sync_service.cloud_http_base", return_value="https://d"),
            patch.object(svc, "_sync_locked", side_effect=slow_locked),
            patch("httpx.AsyncClient") as client_cls,
        ):
            client = AsyncMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=None)
            client.get = AsyncMock(
                return_value=MagicMock(
                    status_code=200,
                    json=lambda: {"assignment": {"scene_id": "orng-bar", "desired_version": 1}},
                    text="",
                )
            )
            client_cls.return_value = client
            await asyncio.gather(
                svc.sync_assigned_release(scene_id="orng-bar", version=1),
                svc.sync_assigned_release(scene_id="orng-bar", version=1),
            )
        # Serialized: enter/leave/enter/leave not enter/enter/leave/leave
        assert order == ["enter", "leave", "enter", "leave"]

    asyncio.run(_run())
