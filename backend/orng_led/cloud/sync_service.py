"""Shared scene sync/apply used by FastAPI ``/api/cloud/sync`` and CloudDeviceAgent.

Never ACK ``applied`` until assignment → download → validate → stage → load →
blackout/disarm → atomic apply → metadata write all succeed.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orng_led.cloud.credentials import load_device_token
from orng_led.cloud.device_mode import should_auto_apply_assigned
from orng_led.cloud.ids import (
    assert_safe_scene_id,
    resolve_under_scenes_root,
    sanitize_staging_token,
)
from orng_led.cloud.metadata import (
    classify_sync_state,
    load_cloud_metadata,
    save_cloud_metadata,
)
from orng_led.cloud.scene_bundle import (
    apply_release_safely,
    pack_scene_bundle,
    unpack_scene_bundle,
    validate_unpacked_release,
    write_bundle_to_dir,
)
from orng_led.config.models import ConfigError

if TYPE_CHECKING:
    from orng_led.api.runtime import AppRuntime

logger = logging.getLogger(__name__)

_SCENE_LOCKS: dict[str, asyncio.Lock] = {}
_LOCKS_GUARD = asyncio.Lock()


class SceneSyncError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def runtime_swap_permitted(
    runtime: Any,
    *,
    auto_apply: bool,
    prefer_apply: bool,
    force: bool,
) -> bool:
    """Permission to follow assigned releases is not permission to interrupt a show.

    The swap runs only when the show is already black. ``auto_apply`` on a
    Raspberry does not force blackout, and neither do sync, retry, or reconnect.
    """
    engine = getattr(runtime, "engine", None)
    overlays = getattr(engine, "overlays", None)
    if not bool(getattr(overlays, "blackout", False)):
        return False
    return bool(force or prefer_apply or auto_apply)


def cloud_http_base() -> str:
    import os

    device_url = os.environ.get("ORNG_CLOUD_DEVICE_URL", "").rstrip("/")
    if not device_url:
        raise SceneSyncError("not_configured", "ORNG_CLOUD_DEVICE_URL not set")
    http_base = device_url.replace("wss://", "https://").replace("ws://", "http://")
    if http_base.endswith("/ws/device"):
        http_base = http_base[: -len("/ws/device")]
    return http_base


async def _lock_for_scene(scene_id: str) -> asyncio.Lock:
    async with _LOCKS_GUARD:
        lock = _SCENE_LOCKS.get(scene_id)
        if lock is None:
            lock = asyncio.Lock()
            _SCENE_LOCKS[scene_id] = lock
        return lock


class SceneSyncService:
    """Pull assigned release and apply safely against AppRuntime."""

    def __init__(self, runtime: AppRuntime) -> None:
        self.runtime = runtime

    async def sync_assigned_release(
        self,
        *,
        force: bool = False,
        scene_id: str | None = None,
        version: int | None = None,
        command_id: str | None = None,
        auto_apply: bool | None = None,
        prefer_apply: bool = False,
    ) -> dict[str, Any]:
        """Full sync pipeline. Returns status dict; raises SceneSyncError on hard fail.

        ``prefer_apply``: remote cloud sync command applies clean updates on desktop
        but still refuses dirty/conflict without ``force``.
        """
        token = load_device_token()
        if not token:
            raise SceneSyncError("no_token", "No device token; complete pairing first")

        http_base = cloud_http_base()
        import httpx

        meta = load_cloud_metadata()
        if auto_apply is None:
            auto_apply = should_auto_apply_assigned()

        async with httpx.AsyncClient(timeout=60.0) as client:
            headers = {"authorization": f"Bearer {token}"}
            assign_res = await client.get(f"{http_base}/device/assignment", headers=headers)
            if assign_res.status_code >= 400:
                raise SceneSyncError(
                    "assignment_failed",
                    f"assignment HTTP {assign_res.status_code}: {assign_res.text[:200]}",
                )
            assignment = (assign_res.json() or {}).get("assignment")
            if not assignment:
                return {"status": "no_assignment"}

            try:
                assigned_scene = assert_safe_scene_id(str(assignment["scene_id"]))
            except ConfigError as exc:
                raise SceneSyncError("bad_scene_id", str(exc)) from exc

            if self.runtime.scene_id == assigned_scene:
                await self._pull_show_prefs(client, headers, http_base)

            desired = assignment.get("desired_version")
            if scene_id:
                try:
                    target_scene = assert_safe_scene_id(scene_id)
                except ConfigError as exc:
                    raise SceneSyncError("bad_scene_id", str(exc)) from exc
            else:
                target_scene = assigned_scene

            target_version = version if version is not None else desired
            if target_version is None:
                return {"status": "no_version", "assignment": assignment}
            target_version = int(target_version)

            if target_scene != assigned_scene and not force:
                raise SceneSyncError(
                    "scene_mismatch",
                    f"Requested {target_scene} but assigned {assigned_scene}",
                )

            scene_lock = await _lock_for_scene(target_scene)
            async with scene_lock:
                return await self._sync_locked(
                    client=client,
                    headers=headers,
                    http_base=http_base,
                    meta=meta,
                    target_scene=target_scene,
                    target_version=target_version,
                    force=force,
                    auto_apply=bool(auto_apply),
                    prefer_apply=prefer_apply,
                    command_id=command_id,
                )

    async def _pull_show_prefs(self, client: Any, headers: dict[str, str], http_base: str) -> None:
        """Copy saved location preferences. Does not publish a scene or toggle sync."""
        try:
            res = await client.get(f"{http_base}/device/location/show-prefs", headers=headers)
        except Exception:
            logger.warning("show prefs fetch failed", exc_info=True)
            return
        if res.status_code == 404 or res.status_code >= 400:
            return
        prefs = (res.json() or {}).get("prefs")
        if not isinstance(prefs, dict):
            return
        try:
            self.runtime.bpm.configure(
                mode=prefs.get("mode"),
                scope=prefs.get("scope"),
                palette=prefs.get("palette"),
                cast=prefs.get("cast"),
                groups=prefs.get("groups") if isinstance(prefs.get("groups"), list) else None,
                excluded=prefs.get("excluded") if isinstance(prefs.get("excluded"), list) else None,
                character=prefs.get("character"),
                intensity=prefs.get("intensity"),
                period=prefs.get("period"),
            )
        except ValueError:
            logger.warning("show prefs rejected")

    async def _sync_locked(
        self,
        *,
        client: Any,
        headers: dict[str, str],
        http_base: str,
        meta: Any,
        target_scene: str,
        target_version: int,
        force: bool,
        auto_apply: bool,
        prefer_apply: bool,
        command_id: str | None,
    ) -> dict[str, Any]:
        scene_meta = meta.scene(target_scene)
        scene_meta.desired_version = target_version
        local_hash = self._local_hash_for_scene(target_scene)

        dl = await client.get(
            f"{http_base}/device/releases/{target_scene}/{target_version}",
            headers=headers,
        )
        if dl.status_code == 403:
            raise SceneSyncError("forbidden", "Release download denied for this credential")
        if dl.status_code >= 400:
            raise SceneSyncError(
                "download_failed",
                f"download HTTP {dl.status_code}: {dl.text[:200]}",
            )

        remote_hash = dl.headers.get("x-content-hash")
        min_app_version = dl.headers.get("x-min-app-version") or None
        scene_meta.desired_hash = remote_hash

        state = classify_sync_state(
            local_hash=local_hash,
            last_synced_hash=scene_meta.last_synced_hash,
            remote_hash=remote_hash,
        )
        scene_meta.sync_status = state

        if state == "synced" and not force:
            # Already matching on disk — still persist applied/last_synced so
            # heartbeat + admin show version/hash across restarts.
            matched = local_hash or remote_hash
            if matched:
                if scene_meta.last_synced_hash is None:
                    scene_meta.last_synced_hash = matched
                if scene_meta.last_synced_version is None:
                    scene_meta.last_synced_version = target_version
                if scene_meta.applied_hash is None:
                    scene_meta.applied_hash = matched
                if scene_meta.applied_version is None:
                    scene_meta.applied_version = target_version
                scene_meta.desired_hash = remote_hash or matched
                scene_meta.desired_version = target_version
                scene_meta.sync_status = "synced"
                scene_meta.last_sync_error = None
            save_cloud_metadata(meta)
            return {
                "status": "synced",
                "scene_id": target_scene,
                "version": target_version,
                "content_hash": matched,
            }

        # Desktop: preserve local edits — never silently overwrite.
        if state in {"conflict", "dirty"} and not force and not auto_apply:
            save_cloud_metadata(meta)
            return {
                "status": state,
                "local_hash": local_hash,
                "remote_hash": remote_hash,
                "last_synced_hash": scene_meta.last_synced_hash,
                "scene_id": target_scene,
                "version": target_version,
            }

        try:
            files, content_hash = unpack_scene_bundle(dl.content)
            validate_unpacked_release(
                files,
                expected_scene_id=target_scene,
                expected_content_hash=remote_hash,
                content_hash=content_hash,
                app_version=getattr(self.runtime, "app_version", None)
                or meta.app_version,
                min_app_version=min_app_version,
            )
        except ConfigError as exc:
            scene_meta.sync_status = "error"
            scene_meta.last_sync_error = str(exc)
            save_cloud_metadata(meta)
            raise SceneSyncError("bundle_invalid", str(exc)) from exc

        store_root = Path(self.runtime.config_root) / "scenes"
        store_root.mkdir(parents=True, exist_ok=True)
        staging_token = sanitize_staging_token(command_id or str(uuid.uuid4()))
        try:
            staging = resolve_under_scenes_root(
                store_root,
                f".staging-{target_scene}-{target_version}-{staging_token}",
            )
            final = resolve_under_scenes_root(store_root, target_scene)
        except ConfigError as exc:
            raise SceneSyncError("path_escape", str(exc)) from exc

        self._cleanup_stale_staging(store_root, target_scene, keep=staging)

        # Re-read blackout here. auto_apply does not skip this, and a clear
        # that lands after the download still leaves the live engine alone.
        if not runtime_swap_permitted(
            self.runtime,
            auto_apply=auto_apply,
            prefer_apply=prefer_apply,
            force=force,
        ):
            self._stage_pending(store_root, target_scene, files)
            scene_meta.sync_status = "update_available"
            scene_meta.last_sync_error = None
            save_cloud_metadata(meta)
            return {
                "status": "deferred",
                "reason": "show_active",
                "remote_hash": remote_hash,
                "scene_id": target_scene,
                "version": target_version,
            }

        self.runtime.scene_apply_in_progress = True
        try:
            if not runtime_swap_permitted(
                self.runtime,
                auto_apply=auto_apply,
                prefer_apply=prefer_apply,
                force=force,
            ):
                self._stage_pending(store_root, target_scene, files)
                scene_meta.sync_status = "update_available"
                scene_meta.last_sync_error = None
                save_cloud_metadata(meta)
                return {
                    "status": "deferred",
                    "reason": "show_active",
                    "remote_hash": remote_hash,
                    "scene_id": target_scene,
                    "version": target_version,
                }
            try:
                apply_release_safely(
                    staging_dir=staging,
                    final_dir=final,
                    files=files,
                    runtime=self.runtime,
                    release_id=f"{target_scene}-v{target_version}",
                    keep_previous_good=True,
                )
            except ConfigError as exc:
                if str(exc) == "show_active":
                    self._stage_pending(store_root, target_scene, files)
                    scene_meta.sync_status = "update_available"
                    scene_meta.last_sync_error = None
                    save_cloud_metadata(meta)
                    return {
                        "status": "deferred",
                        "reason": "show_active",
                        "remote_hash": remote_hash,
                        "scene_id": target_scene,
                        "version": target_version,
                    }
                scene_meta.sync_status = "error"
                scene_meta.last_sync_error = str(exc)
                save_cloud_metadata(meta)
                raise SceneSyncError("apply_failed", str(exc)) from exc
        finally:
            self.runtime.scene_apply_in_progress = False

        # Venue output comes back only under the blackout the show already had.
        if runtime_swap_permitted(
            self.runtime,
            auto_apply=True,
            prefer_apply=True,
            force=False,
        ):
            try:
                self.runtime._ensure_venue_show_ready()
            except Exception:  # noqa: BLE001
                logger.debug("venue restore after scene apply failed", exc_info=True)

        scene_meta.last_synced_version = target_version
        scene_meta.last_synced_hash = content_hash
        scene_meta.applied_version = target_version
        scene_meta.applied_hash = content_hash
        scene_meta.desired_version = target_version
        scene_meta.desired_hash = content_hash
        scene_meta.sync_status = "synced"
        scene_meta.last_sync_error = None
        save_cloud_metadata(meta)

        try:
            await self.runtime.broadcast_state()
        except Exception:  # noqa: BLE001
            logger.debug("broadcast_state after sync failed", exc_info=True)

        return {
            "status": "applied",
            "scene_id": target_scene,
            "version": target_version,
            "content_hash": content_hash,
        }

    def _stage_pending(
        self,
        store_root: Path,
        scene_id: str,
        files: dict[str, bytes],
    ) -> None:
        """Keep a validated copy beside the live scene. Does not touch the engine."""
        import shutil

        pending = store_root / f".pending-{scene_id}"
        if pending.exists():
            shutil.rmtree(pending, ignore_errors=True)
        write_bundle_to_dir(files, pending)

    def _local_hash_for_scene(self, scene_id: str) -> str | None:
        try:
            assert_safe_scene_id(scene_id)
            store_root = Path(self.runtime.config_root) / "scenes"
            scene_dir = resolve_under_scenes_root(store_root, scene_id)
        except ConfigError:
            return None
        if not scene_dir.is_dir():
            if self.runtime.scene_id == scene_id:
                scene_dir = Path(self.runtime.config_dir)
            else:
                return None
        try:
            _raw, manifest = pack_scene_bundle(scene_dir)
            return str(manifest.get("content_sha256") or "") or None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _cleanup_stale_staging(
        store_root: Path,
        scene_id: str,
        *,
        keep: Path | None = None,
    ) -> None:
        if not store_root.is_dir():
            return
        prefix = f".staging-{scene_id}-"
        keep_resolved = keep.resolve() if keep is not None else None
        for child in store_root.iterdir():
            if not (child.name.startswith(prefix) and child.is_dir()):
                continue
            if keep_resolved is not None and child.resolve() == keep_resolved:
                continue
            try:
                import shutil

                shutil.rmtree(child, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
