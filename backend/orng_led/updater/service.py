"""Application update orchestration — stage / apply / rollback / crash recovery.

Never replaces developer git checkouts. Never touches scenes/credentials/metadata.
Does not arm Art-Net after update.

Production apply writes an apply-request for the external bootstrap helper and
returns ``restarting`` after the HTTP response path can complete. ``simulate``
is test-only (pytest / ORNG_UPDATER_SIMULATE) — production routes must not pass
``simulate=True``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orng_led.updater.errors import AppUpdateError
from orng_led.updater.lock import async_update_lock, interprocess_update_lock
from orng_led.updater.mode import InstallMode, detect_arch, detect_install_mode, detect_platform
from orng_led.updater.package import AppPackageError, unpack_and_verify
from orng_led.updater.paths import (
    apply_request_path,
    assert_release_path_contained,
    ensure_managed_layout,
    read_pointer,
    release_dir,
    resolve_managed_paths,
    staging_dir,
    write_pointer_atomic,
)
from orng_led.updater.state import (
    UpdateState,
    load_update_state,
    save_update_state,
    transition,
)
from orng_led.version import app_version, build_id, updater_protocol_version

if TYPE_CHECKING:
    from orng_led.api.runtime import AppRuntime

logger = logging.getLogger(__name__)


def describe_update_phase(
    *,
    state: str,
    last_error: str | None,
    running_build: str,
    desired_build: str | None,
    staged_build: str | None,
) -> str:
    """Separate check/download/stage/restart/applied/error for the operator UI."""
    if state == UpdateState.FAILED.value or (state == UpdateState.IDLE.value and last_error):
        return "error"
    if state == UpdateState.WAITING_FOR_SAFE_STATE.value:
        return "waiting"
    if state in {
        UpdateState.APPLYING.value,
        UpdateState.RESTARTING.value,
        UpdateState.HEALTH_CHECK.value,
        UpdateState.ROLLING_BACK.value,
    }:
        return "restarting"
    if state in {UpdateState.DOWNLOADING.value, UpdateState.VERIFYING.value, UpdateState.CHECKING.value}:
        return "downloading" if state != UpdateState.CHECKING.value else "checking"
    if state == UpdateState.STAGED.value and staged_build:
        return "downloaded"
    if state in {UpdateState.HEALTHY.value, UpdateState.ROLLED_BACK.value}:
        return "applied"
    if desired_build and desired_build != running_build:
        return "available"
    if desired_build and desired_build == running_build:
        return "applied"
    return "idle"

# Re-export for existing imports.
__all__ = ["AppUpdateError", "AppUpdateService"]


def _simulate_default() -> bool:
    return os.environ.get("ORNG_UPDATER_SIMULATE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    } or bool(os.environ.get("PYTEST_CURRENT_TEST"))


class AppUpdateService:
    def __init__(self, runtime: AppRuntime | None = None) -> None:
        self.runtime = runtime
        self.paths = resolve_managed_paths()

    def status(self) -> dict[str, Any]:
        mode = detect_install_mode()
        record = load_update_state(self.paths)
        current = read_pointer(self.paths.current_pointer) or {}
        previous = read_pointer(self.paths.previous_pointer) or {}
        return {
            "install_mode": mode.value,
            "self_update_allowed": mode == InstallMode.MANAGED,
            "refusal_reason": (
                "developer_checkout" if mode == InstallMode.DEVELOPER else None
            ),
            "app_version": app_version(),
            "build_id": build_id(),
            "platform": detect_platform(),
            "arch": detect_arch(),
            "updater_protocol_version": updater_protocol_version(),
            "current_release": current,
            "previous_release": previous,
            "update_state": record.state,
            "update_progress": record.progress,
            "desired_version": record.desired_version,
            "desired_build_id": record.desired_build_id,
            "staged_version": record.staged_version,
            "staged_build_id": record.staged_build_id,
            "staged_content_sha256": record.staged_content_sha256,
            "update_phase": describe_update_phase(
                state=record.state,
                last_error=record.last_error,
                running_build=str(current.get("build_id") or build_id() or ""),
                desired_build=record.desired_build_id,
                staged_build=record.staged_build_id,
            ),
            "last_error": record.last_error,
            "last_successful_version": record.last_successful_version,
            "pending_result": record.pending_result,
        }

    def refuse_if_developer(self) -> None:
        if detect_install_mode() == InstallMode.DEVELOPER:
            raise AppUpdateError(
                "developer_checkout",
                "Remote self-update is disabled for developer git checkouts",
            )

    async def check_assigned(self) -> dict[str, Any]:
        self.refuse_if_developer()
        record = load_update_state(self.paths)
        prior_state = record.state
        # A check must not drop a package that is already staged.
        if prior_state != UpdateState.STAGED.value:
            transition(record, UpdateState.CHECKING, paths=self.paths, progress=0.05)
        from orng_led.cloud.credentials import load_device_token
        from orng_led.cloud.sync_service import cloud_http_base

        def _fail(message: str) -> None:
            if prior_state == UpdateState.STAGED.value:
                return
            transition(record, UpdateState.FAILED, paths=self.paths, error=message[:300])

        token = load_device_token()
        if not token:
            _fail("no_token")
            raise AppUpdateError("no_token", "No device token")

        import httpx

        http_base = cloud_http_base()
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.get(
                f"{http_base}/device/app/assignment",
                headers={"authorization": f"Bearer {token}"},
            )
            if res.status_code >= 400:
                _fail(f"assignment HTTP {res.status_code}")
                raise AppUpdateError("assignment_failed", res.text[:200])
            assignment = (res.json() or {}).get("assignment") or {}
        desired_v = assignment.get("desired_version")
        desired_b = assignment.get("desired_build_id")
        # A cloud assignment must not replace a package that is already staged.
        if (
            prior_state == UpdateState.STAGED.value
            and record.staged_build_id
            and (
                str(desired_b or "") != str(record.staged_build_id)
                or (
                    desired_v
                    and record.staged_version
                    and str(desired_v) != str(record.staged_version)
                )
            )
        ):
            desired_v = record.staged_version
            desired_b = record.staged_build_id
        # A finished check is not a staged package and not an apply.
        # Keep STAGED only when a package was already staged before this check.
        after = (
            UpdateState.STAGED
            if prior_state == UpdateState.STAGED.value
            else UpdateState.IDLE
        )
        transition(
            record,
            after,
            paths=self.paths,
            progress=1.0 if after == UpdateState.IDLE else record.progress,
            desired_version=desired_v,
            desired_build_id=desired_b,
            error="",
        )
        available = bool(
            desired_v
            and (
                desired_v != app_version()
                or (desired_b and desired_b != build_id())
            )
        )
        return {
            "result": "checked",
            "assignment": assignment,
            "update_available": available,
            "auto_stage": bool(assignment.get("auto_stage")),
            "status": self.status(),
        }

    async def stage(
        self,
        *,
        version: str | None = None,
        build_id_wanted: str | None = None,
        command_id: str | None = None,
        package_bytes: bytes | None = None,
        manifest: dict[str, Any] | None = None,
        signature_b64: str | None = None,
        key_id: str | None = None,
    ) -> dict[str, Any]:
        self.refuse_if_developer()
        if package_bytes is None:
            record = load_update_state(self.paths)
            staged = str(record.staged_build_id or "")
            wanted = str(build_id_wanted or "")
            if (
                record.state == UpdateState.STAGED.value
                and staged
                and wanted != staged
            ):
                raise AppUpdateError(
                    "staged_candidate",
                    "A staged package is already the candidate",
                )
        token = command_id or str(uuid.uuid4())
        async with async_update_lock():
            await asyncio.to_thread(self._mark_downloading, token)
            try:
                if package_bytes is None or manifest is None:
                    (
                        package_bytes,
                        manifest,
                        signature_b64,
                        key_id,
                    ) = await self._download_assigned(
                        version=version,
                        build_id_wanted=build_id_wanted,
                    )
            except Exception as exc:
                await asyncio.to_thread(self._fail_download_if_current, token, str(exc))
                raise
            return await asyncio.to_thread(
                self._commit_downloaded,
                token,
                package_bytes,
                manifest,
                signature_b64,
                key_id,
            )

    def _mark_downloading(self, token: str) -> None:
        with interprocess_update_lock(self.paths):
            ensure_managed_layout(self.paths)
            record = load_update_state(self.paths)
            if record.state in {
                UpdateState.APPLYING.value,
                UpdateState.RESTARTING.value,
                UpdateState.HEALTH_CHECK.value,
                UpdateState.ROLLING_BACK.value,
            }:
                raise AppUpdateError(
                    "apply_in_progress",
                    "An apply or rollback is already running",
                )
            transition(
                record,
                UpdateState.DOWNLOADING,
                paths=self.paths,
                progress=0.2,
                command_id=token,
            )

    def _fail_download_if_current(self, token: str, error: str) -> None:
        with interprocess_update_lock(self.paths):
            record = load_update_state(self.paths)
            if (
                record.command_id == token
                and record.state == UpdateState.DOWNLOADING.value
            ):
                transition(
                    record,
                    UpdateState.FAILED,
                    paths=self.paths,
                    error=error[:300],
                )

    def _commit_downloaded(
        self,
        token: str,
        package_bytes: bytes | None,
        manifest: dict[str, Any] | None,
        signature_b64: str | None,
        key_id: str | None,
    ) -> dict[str, Any]:
        with interprocess_update_lock(self.paths):
            record = load_update_state(self.paths)
            if (
                record.command_id != token
                or record.state != UpdateState.DOWNLOADING.value
            ):
                return {"status": "cancelled", "result": "cancelled", "staged": False}
            return self._stage_locked(
                version=None,
                build_id_wanted=None,
                command_id=token,
                package_bytes=package_bytes,
                manifest=manifest,
                signature_b64=signature_b64,
                key_id=key_id,
                already_marked=True,
            )

    def _stage_locked(
        self,
        *,
        version: str | None,
        build_id_wanted: str | None,
        command_id: str | None,
        package_bytes: bytes | None,
        manifest: dict[str, Any] | None,
        signature_b64: str | None,
        key_id: str | None,
        already_marked: bool = False,
    ) -> dict[str, Any]:
        ensure_managed_layout(self.paths)
        record = load_update_state(self.paths)
        if not already_marked:
            transition(
                record,
                UpdateState.DOWNLOADING,
                paths=self.paths,
                progress=0.2,
                command_id=command_id,
            )

        if package_bytes is None or manifest is None:
            # Sync wrapper around async download for lock boundary.
            package_bytes, manifest, signature_b64, key_id = asyncio.run(
                self._download_assigned(
                    version=version,
                    build_id_wanted=build_id_wanted,
                )
            )

        assert package_bytes is not None and manifest is not None
        version = str(manifest["version"])
        build_id_wanted = str(manifest["build_id"])
        content_hash = str(manifest.get("content_sha256") or "")

        dest = release_dir(self.paths, version, build_id_wanted)
        # Idempotent: identity in RELEASE.json; package hash in install-meta (not signed tree).
        install_meta_path = (
            self.paths.app_root / "install-meta" / f"{version}-{build_id_wanted}.json"
        )
        if dest.is_dir() and (dest / "RELEASE.json").is_file():
            try:
                existing = json.loads((dest / "RELEASE.json").read_text(encoding="utf-8"))
                stored_hash = None
                if install_meta_path.is_file():
                    stored_hash = json.loads(
                        install_meta_path.read_text(encoding="utf-8")
                    ).get("content_sha256")
                if stored_hash and content_hash and stored_hash != content_hash:
                    raise AppUpdateError(
                        "identity_conflict",
                        f"Release {version}-{build_id_wanted} hash conflict",
                    )
                hash_ok = (not content_hash) or (stored_hash in {None, content_hash})
                if (
                    existing.get("version") == version
                    and existing.get("build_id") == build_id_wanted
                    and hash_ok
                ):
                    transition(
                        record,
                        UpdateState.STAGED,
                        paths=self.paths,
                        progress=0.8,
                        staged_version=version,
                        staged_build_id=build_id_wanted,
                        staged_path=str(dest),
                        staged_content_sha256=content_hash or None,
                        last_error=None,
                    )
                    return {
                        "status": "staged",
                        "path": str(dest),
                        "version": version,
                        "build_id": build_id_wanted,
                        "idempotent": True,
                    }
            except AppUpdateError:
                raise
            except (OSError, json.JSONDecodeError):
                pass

        # Never replace current/previous in place.
        current = read_pointer(self.paths.current_pointer) or {}
        previous = read_pointer(self.paths.previous_pointer) or {}
        for ptr in (current, previous):
            p = ptr.get("path")
            if p and Path(p).resolve() == dest.resolve():
                raise AppUpdateError(
                    "active_release",
                    "Refusing to restage an active current/previous release path",
                )

        transition(
            record,
            UpdateState.VERIFYING,
            paths=self.paths,
            progress=0.5,
            desired_version=version,
            desired_build_id=build_id_wanted,
        )

        staging_id = str(uuid.uuid4())
        staging = staging_dir(self.paths, staging_id)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.parent.mkdir(parents=True, exist_ok=True)
        try:
            written = unpack_and_verify(
                package_bytes,
                manifest=manifest,
                signature_b64=str(signature_b64 or ""),
                key_id=str(key_id or ""),
                dest=staging,
                expected_platform=detect_platform(),
                expected_arch=detect_arch(),
            )
            # Signed tree is immutable — never rewrite RELEASE.json after verify.
            release_meta_path = staging / "RELEASE.json"
            if not release_meta_path.is_file():
                raise AppUpdateError("missing_release_json", "RELEASE.json missing after unpack")
            release_meta = json.loads(release_meta_path.read_text(encoding="utf-8"))
            for field in ("version", "build_id", "platform", "arch", "channel", "entrypoint"):
                expected = manifest.get(field)
                if expected is None:
                    continue
                actual = release_meta.get(field)
                if actual is not None and actual != expected:
                    raise AppUpdateError(
                        "release_meta_mismatch",
                        f"RELEASE.json {field}={actual!r} != manifest {expected!r}",
                    )
            if str(release_meta.get("version")) != version or str(
                release_meta.get("build_id")
            ) != build_id_wanted:
                raise AppUpdateError(
                    "release_identity_mismatch",
                    f"{release_meta.get('version')}-{release_meta.get('build_id')} "
                    f"!= {version}-{build_id_wanted}",
                )
            # Re-hash staged files must match signed manifest file digests.
            expected_files = manifest.get("files") or {}
            for rel, digest in expected_files.items():
                staged_file = staging / rel
                if not staged_file.is_file():
                    raise AppUpdateError("missing_staged_file", rel)
                got = hashlib.sha256(staged_file.read_bytes()).hexdigest()
                if got != digest:
                    raise AppUpdateError("staged_hash_mismatch", rel)
            # Installation metadata stays outside the signed release tree.
            install_meta = {
                "version": version,
                "build_id": build_id_wanted,
                "content_sha256": content_hash,
                "key_id": key_id,
                "staged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            meta_dir = self.paths.app_root / "install-meta"
            meta_dir.mkdir(parents=True, exist_ok=True)
            (meta_dir / f"{version}-{build_id_wanted}.json").write_text(
                json.dumps(install_meta, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            if str(manifest.get("platform") or detect_platform()) == "raspberry":
                from orng_led.raspberry.runtime_validate import (
                    RuntimeValidationError,
                    validate_release_runtime,
                )

                try:
                    validate_release_runtime(staging)
                except RuntimeValidationError as exc:
                    raise AppUpdateError(exc.code, exc.message) from exc
            _ = written
            assert_release_path_contained(self.paths, staging)

            if dest.exists():
                # Conflicting incomplete dir — only remove if not active.
                shutil.rmtree(dest, ignore_errors=True)
            os.replace(staging, dest)
            assert_release_path_contained(self.paths, dest)
        except (AppPackageError, AppUpdateError, ValueError, OSError) as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            code = getattr(exc, "code", "verify_failed")
            transition(
                record,
                UpdateState.FAILED,
                paths=self.paths,
                error=str(exc),
            )
            if isinstance(exc, AppUpdateError):
                raise
            raise AppUpdateError(str(code), str(exc)) from exc

        transition(
            record,
            UpdateState.STAGED,
            paths=self.paths,
            progress=0.8,
            staged_version=version,
            staged_build_id=build_id_wanted,
            staged_path=str(dest),
            staged_content_sha256=content_hash or None,
            last_error=None,
        )
        return {
            "status": "staged",
            "path": str(dest),
            "version": version,
            "build_id": build_id_wanted,
            "idempotent": False,
        }

    async def _download_assigned(
        self,
        *,
        version: str | None,
        build_id_wanted: str | None,
    ) -> tuple[bytes, dict[str, Any], str, str]:
        from orng_led.updater.download import AppDownloadError, download_assigned_app_release

        try:
            dl = await download_assigned_app_release(
                version=version,
                build_id=build_id_wanted,
            )
        except AppDownloadError as exc:
            raise AppUpdateError(exc.code, exc.message) from exc

        # Prefer package hash from contract.
        if dl.content_sha256:
            actual = hashlib.sha256(dl.package_bytes).hexdigest()
            if actual != dl.content_sha256:
                raise AppUpdateError("hash_mismatch", "Downloaded package hash mismatch")
        return dl.package_bytes, dl.manifest, dl.signature_b64, dl.key_id

    def apply(
        self,
        *,
        expected_version: str | None = None,
        expected_build_id: str | None = None,
        expected_content_sha256: str | None = None,
        force_unsafe: bool = False,
        command_id: str | None = None,
        simulate: bool | None = None,
    ) -> dict[str, Any]:
        """Request pointer swap for one exact staged release.

        Identity is checked before blackout and again under the update lock
        before the helper request is written. A successful check is not a
        staged package. Unknown output posture is not treated as disarmed.
        Returns ``restarting`` (not ``applied``). Applied is reported only after
        helper health success via pending_result / update_result.
        """
        self.refuse_if_developer()
        if simulate is None:
            simulate = _simulate_default()
        version_wanted = str(expected_version or "").strip()
        build_wanted = str(expected_build_id or "").strip()
        hash_wanted = str(expected_content_sha256 or "").strip().lower() or None
        if not version_wanted or not build_wanted:
            raise AppUpdateError(
                "identity_required",
                "apply requires the expected version and build_id",
            )

        with interprocess_update_lock(self.paths):
            record = load_update_state(self.paths)
            if command_id:
                record.command_id = command_id
            self._assert_apply_identity(record, version_wanted, build_wanted, hash_wanted)

            armed = self._known_output_armed()
            # In-process simulation has no venue output. A live runtime whose
            # armed flag cannot be read must not look disarmed.
            if armed is None and not (simulate and self.runtime is None):
                raise AppUpdateError(
                    "output_state_unknown",
                    "Output armed state is unknown; refusing blackout and apply",
                )
            if armed is True and not force_unsafe:
                transition(
                    record,
                    UpdateState.WAITING_FOR_SAFE_STATE,
                    paths=self.paths,
                    progress=0.85,
                )
                return {
                    "status": "waiting_for_safe_state",
                    "message": "Device is armed; apply stays waiting",
                }

            if armed is not None:
                self._enter_safe_lighting(require_confirm=True)
            record = load_update_state(self.paths)
            if command_id:
                record.command_id = command_id
            self._assert_apply_identity(record, version_wanted, build_wanted, hash_wanted)
            transition(record, UpdateState.APPLYING, paths=self.paths, progress=0.9)

            staged = Path(str(record.staged_path))
            try:
                staged = assert_release_path_contained(self.paths, staged)
            except ValueError as exc:
                transition(record, UpdateState.FAILED, paths=self.paths, error=str(exc))
                raise AppUpdateError("bad_staged_path", str(exc)) from exc
            if not staged.is_dir():
                raise AppUpdateError("staged_missing", str(staged))
            version = version_wanted
            bid = build_wanted
            self._assert_pointer_identity(staged, version, bid)

            req = {
                "command_id": record.command_id,
                "expected_version": version,
                "expected_build_id": bid,
                "staged_path": str(staged),
                "requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "simulate": bool(simulate),
            }
            write_pointer_atomic(apply_request_path(self.paths), req)
            transition(
                record,
                UpdateState.RESTARTING,
                paths=self.paths,
                progress=0.95,
                pending_result={
                    "command_id": record.command_id,
                    "status": "restarting",
                    "version": version,
                    "build_id": bid,
                },
            )

            if simulate:
                # In-process helper simulation (subprocess tests use real helper).
                from orng_led.updater import helper_protocol

                outcome = helper_protocol.apply_staged_release(
                    self.paths,
                    simulate_health_fail=os.environ.get("ORNG_UPDATER_HEALTH_FAIL", "").strip()
                    in {"1", "true"},
                    simulate_crash_after_swap=os.environ.get(
                        "ORNG_UPDATER_CRASH_AFTER_SWAP", ""
                    ).strip()
                    in {"1", "true"},
                    simulate_crash_before_swap=os.environ.get(
                        "ORNG_UPDATER_CRASH_BEFORE_SWAP", ""
                    ).strip()
                    in {"1", "true"},
                    skip_process=True,
                )
                return outcome

            # Production: API returns; helper performs swap + health + final result.
            # Soft-signal runtime shutdown so the response can flush first.
            self._request_runtime_shutdown()
            return {
                "status": "restarting",
                "version": version,
                "build_id": bid,
                "message": "Apply accepted; external helper will restart and health-check",
            }

    def rollback(
        self,
        *,
        command_id: str | None = None,
        simulate: bool | None = None,
    ) -> dict[str, Any]:
        """Request rollback via external helper — same lifecycle as apply.

        Production never swaps pointers in-process. Writes ``apply-request.json``
        with ``action=rollback``, returns ``restarting``, and the bootstrap helper
        stops the child, switches to previous, health-checks, and restores the
        working candidate if previous is unhealthy.
        """
        self.refuse_if_developer()
        if simulate is None:
            simulate = _simulate_default()

        with interprocess_update_lock(self.paths):
            record = load_update_state(self.paths)
            if command_id:
                record.command_id = command_id
                save_update_state(record, self.paths)

            previous = read_pointer(self.paths.previous_pointer)
            if not previous:
                raise AppUpdateError("no_previous", "No previous release to roll back to")
            current = read_pointer(self.paths.current_pointer) or {}
            try:
                prev_path = assert_release_path_contained(
                    self.paths, Path(str(previous.get("path") or ""))
                )
            except ValueError as exc:
                raise AppUpdateError("bad_previous_path", str(exc)) from exc
            if not prev_path.is_dir():
                raise AppUpdateError("previous_missing", str(prev_path))
            self._assert_pointer_identity(
                prev_path,
                str(previous.get("version") or ""),
                str(previous.get("build_id") or ""),
            )

            self._enter_safe_lighting(require_confirm=True)
            transition(record, UpdateState.ROLLING_BACK, paths=self.paths, progress=0.9)

            req = {
                "action": "rollback",
                "command_id": record.command_id,
                "expected_version": str(previous.get("version") or ""),
                "expected_build_id": str(previous.get("build_id") or ""),
                "restore_version": str(current.get("version") or ""),
                "restore_build_id": str(current.get("build_id") or ""),
                "restore_path": str(current.get("path") or ""),
                "requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "simulate": bool(simulate),
            }
            write_pointer_atomic(apply_request_path(self.paths), req)
            transition(
                record,
                UpdateState.RESTARTING,
                paths=self.paths,
                progress=0.95,
                pending_result={
                    "command_id": record.command_id,
                    "status": "restarting",
                    "version": previous.get("version"),
                    "build_id": previous.get("build_id"),
                    "action": "rollback",
                },
            )

            if simulate:
                from orng_led.updater import helper_protocol

                restore = None
                if current.get("path"):
                    restore = {
                        "version": current.get("version"),
                        "build_id": current.get("build_id"),
                        "path": current.get("path"),
                    }
                return helper_protocol.rollback_to_previous(
                    self.paths,
                    command_id=record.command_id,
                    skip_process=True,
                    simulate_health_fail=os.environ.get(
                        "ORNG_UPDATER_HEALTH_FAIL", ""
                    ).strip()
                    in {"1", "true"},
                    restore_override=restore,
                    reason="admin_rollback",
                )

            self._request_runtime_shutdown()
            return {
                "status": "restarting",
                "version": previous.get("version"),
                "build_id": previous.get("build_id"),
                "action": "rollback",
                "message": "Rollback accepted; external helper will restart previous",
            }

    def cancel_staged(self) -> dict[str, Any]:
        with interprocess_update_lock(self.paths):
            record = load_update_state(self.paths)
            if record.staged_path:
                staged = Path(record.staged_path)
                try:
                    staged = assert_release_path_contained(self.paths, staged)
                    current = read_pointer(self.paths.current_pointer) or {}
                    previous = read_pointer(self.paths.previous_pointer) or {}
                    active = {
                        Path(p).resolve()
                        for p in (current.get("path"), previous.get("path"))
                        if p
                    }
                    if staged.resolve() not in active and staged.is_dir():
                        # Only delete this staged path (not current/previous).
                        shutil.rmtree(staged, ignore_errors=True)
                except ValueError:
                    pass
            # Drop any leftover staging dirs for this cancel.
            staging_root = self.paths.releases / ".staging"
            if staging_root.is_dir():
                for child in staging_root.iterdir():
                    shutil.rmtree(child, ignore_errors=True)
            transition(
                record,
                UpdateState.IDLE,
                paths=self.paths,
                progress=0.0,
                staged_path=None,
                staged_version=None,
                staged_build_id=None,
                staged_content_sha256=None,
                command_id=None,
                last_error=None,
            )
            return {"status": "idle"}

    def recover_after_crash(self) -> dict[str, Any]:
        """Continue or roll back based on persisted state. Called by helper/runtime startup."""
        record = load_update_state(self.paths)
        state = record.state
        if state in {
            UpdateState.IDLE.value,
            UpdateState.HEALTHY.value,
            UpdateState.ROLLED_BACK.value,
            UpdateState.STAGED.value,
        }:
            return {"status": state, "action": "none"}
        if state in {
            UpdateState.DOWNLOADING.value,
            UpdateState.VERIFYING.value,
            UpdateState.CHECKING.value,
        }:
            # Clean orphan staging dirs; never touch current/previous.
            staging_root = self.paths.releases / ".staging"
            if staging_root.is_dir():
                for child in staging_root.iterdir():
                    shutil.rmtree(child, ignore_errors=True)
            transition(record, UpdateState.FAILED, paths=self.paths, error="interrupted")
            return {"status": "failed", "action": "reset"}
        if state in {
            UpdateState.APPLYING.value,
            UpdateState.RESTARTING.value,
            UpdateState.HEALTH_CHECK.value,
            UpdateState.ROLLING_BACK.value,
        }:
            from orng_led.updater import helper_protocol

            req = read_pointer(apply_request_path(self.paths)) or {}
            action = str(req.get("action") or "apply")
            # Intentional apply/rollback is owned by the external bootstrap helper.
            # The new release process must NOT steal pointers during helper health —
            # that race was reporting crash_recovery and rolling back healthy candidates.
            if req and action != "rollback":
                return {
                    "status": state,
                    "action": "await_helper",
                    "command_id": req.get("command_id") or record.command_id,
                }
            # The process just launched from current.json still carries that
            # build id. Moving the pointer here is what left a new process
            # running while current.json pointed at the previous release.
            running = os.environ.get("ORNG_BUILD_ID", "").strip()
            current = read_pointer(self.paths.current_pointer) or {}
            if running and running == str(current.get("build_id") or ""):
                return {
                    "status": state,
                    "action": "await_helper",
                    "command_id": record.command_id,
                }
            try:
                if action == "rollback" and req:
                    restore = None
                    if req.get("restore_path"):
                        restore = {
                            "version": req.get("restore_version"),
                            "build_id": req.get("restore_build_id"),
                            "path": req.get("restore_path"),
                        }
                    return helper_protocol.rollback_to_previous(
                        self.paths,
                        command_id=str(req.get("command_id") or record.command_id or "")
                        or None,
                        skip_process=True,
                        restore_override=restore,
                        reason="crash_recovery",
                    )
                # No apply-request: true mid-flight crash — restore previous.
                return helper_protocol.rollback_to_previous(
                    self.paths,
                    command_id=record.command_id,
                    skip_process=True,
                    reason="crash_recovery",
                )
            except AppUpdateError:
                transition(
                    record, UpdateState.FAILED, paths=self.paths, error="crash_recovery"
                )
                return {"status": "failed", "action": "manual"}
        return {"status": state, "action": "none"}

    def peek_pending_result(self) -> dict[str, Any] | None:
        return load_update_state(self.paths).pending_result

    def clear_pending_result(self) -> None:
        """Clear pending only after server ACK/2xx."""
        record = load_update_state(self.paths)
        record.pending_result = None
        if record.state in {UpdateState.HEALTHY.value, UpdateState.ROLLED_BACK.value}:
            transition(record, UpdateState.IDLE, paths=self.paths, pending_result=None)
        else:
            save_update_state(record, self.paths)

    def consume_pending_result(self) -> dict[str, Any] | None:
        """Deprecated: clears before ACK. Prefer peek + clear_pending_result."""
        pending = self.peek_pending_result()
        if pending:
            self.clear_pending_result()
        return pending

    def _known_output_armed(self) -> bool | None:
        """True or False when the output flag is a real bool. None if unknown."""
        rt = self.runtime
        if rt is None:
            return None
        try:
            value = rt.output.armed
        except Exception:  # noqa: BLE001
            return None
        if isinstance(value, bool):
            return value
        return None

    def _assert_apply_identity(
        self,
        record: Any,
        expected_version: str,
        expected_build_id: str,
        expected_content_sha256: str | None,
    ) -> None:
        if record.state != UpdateState.STAGED.value:
            raise AppUpdateError("not_staged", f"state is {record.state}")
        if (
            record.desired_version
            and str(record.desired_version) != expected_version
        ) or (
            record.desired_build_id
            and str(record.desired_build_id) != expected_build_id
        ):
            raise AppUpdateError(
                "assignment_replaced",
                "Current assignment does not match this apply",
            )
        if (
            str(record.staged_version or "") != expected_version
            or str(record.staged_build_id or "") != expected_build_id
        ):
            raise AppUpdateError(
                "identity_mismatch",
                "Staged package is not the requested release",
            )
        staged = Path(str(record.staged_path or ""))
        if not staged.is_dir():
            raise AppUpdateError("not_staged", "Staged directory missing")
        self._assert_pointer_identity(staged, expected_version, expected_build_id)
        if expected_content_sha256:
            got = str(record.staged_content_sha256 or "").lower()
            if not got:
                meta_path = (
                    self.paths.app_root
                    / "install-meta"
                    / f"{expected_version}-{expected_build_id}.json"
                )
                if meta_path.is_file():
                    try:
                        got = str(
                            json.loads(meta_path.read_text(encoding="utf-8")).get(
                                "content_sha256"
                            )
                            or ""
                        ).lower()
                    except (OSError, json.JSONDecodeError):
                        got = ""
            if not got or got != expected_content_sha256:
                raise AppUpdateError(
                    "identity_mismatch",
                    "Staged content hash does not match the requested release",
                )

    def _assert_pointer_identity(self, path: Path, version: str, build: str) -> None:
        meta_path = path / "RELEASE.json"
        if not meta_path.is_file():
            raise AppUpdateError("missing_release_meta", str(meta_path))
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("version") != version or meta.get("build_id") != build:
            raise AppUpdateError(
                "identity_mismatch",
                f"RELEASE.json {meta.get('version')}-{meta.get('build_id')} != {version}-{build}",
            )

    def _enter_safe_lighting(self, *, require_confirm: bool) -> None:
        rt = self.runtime
        if rt is None:
            if require_confirm and os.environ.get("ORNG_UPDATER_REQUIRE_RUNTIME", "").strip() in {
                "1",
                "true",
            }:
                raise AppUpdateError("no_runtime", "Safe lighting requires runtime")
            return
        errors: list[str] = []
        try:
            if getattr(rt, "input_dispatcher", None) is not None:
                rt.input_dispatcher.force_release_all()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"release_holds:{exc}")
        try:
            rt.engine.set_blackout(True)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"blackout:{exc}")
        try:
            rt.output._emit_zero_frames(count=3, raise_on_error=True)  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            errors.append(f"dark_frames:{exc}")
        try:
            rt.output.disarm()
        except Exception as exc:  # noqa: BLE001
            try:
                rt.output.armed = False
            except Exception as exc2:  # noqa: BLE001
                errors.append(f"disarm:{exc}/{exc2}")

        # Confirm actual posture.
        try:
            if not bool(rt.engine.overlays.blackout):
                errors.append("blackout_not_set")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"blackout_read:{exc}")
        try:
            if bool(rt.output.armed):
                errors.append("still_armed")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"armed_read:{exc}")

        if errors and require_confirm:
            raise AppUpdateError("safe_lighting_failed", "; ".join(errors))

    def _ensure_disarmed_blackout(self) -> None:
        self._enter_safe_lighting(require_confirm=False)

    def _request_runtime_shutdown(self) -> None:
        rt = self.runtime
        if rt is None:
            return
        # Soft flag — helper/supervisor owns the process lifecycle.
        try:
            rt._updater_shutdown_requested = True
        except Exception:  # noqa: BLE001
            pass
