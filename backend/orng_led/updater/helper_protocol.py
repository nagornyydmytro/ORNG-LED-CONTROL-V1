"""Shared apply/rollback/health protocol used by bootstrap helper and tests.

Pointer swap happens HERE after safe shutdown — not as a fake instantaneous
``applied``. Production bootstrap helper lives outside replaceable releases.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from orng_led.updater.errors import AppUpdateError
from orng_led.updater.paths import (
    ManagedPaths,
    apply_request_path,
    assert_release_path_contained,
    read_pointer,
    write_pointer_atomic,
)
from orng_led.updater.state import UpdateState, load_update_state, transition


class HelperError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _load_release_meta(path: Path) -> dict[str, Any]:
    meta = path / "RELEASE.json"
    if not meta.is_file():
        raise HelperError("missing_release_meta", str(meta))
    return json.loads(meta.read_text(encoding="utf-8"))


def validate_pointer_target(paths: ManagedPaths, pointer: dict[str, Any]) -> Path:
    raw = pointer.get("path")
    if not raw:
        raise HelperError("bad_pointer", "Missing path")
    target = Path(str(raw))
    try:
        target = assert_release_path_contained(paths, target)
    except ValueError as exc:
        raise HelperError("path_escape", str(exc)) from exc
    if not target.is_dir():
        raise HelperError("missing_release", str(target))
    if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != paths.app_root):
        # Target itself must not be a symlink.
        if target.is_symlink():
            raise HelperError("symlink", str(target))
    version = str(pointer.get("version") or "")
    build = str(pointer.get("build_id") or "")
    meta = _load_release_meta(target)
    if meta.get("version") != version or meta.get("build_id") != build:
        raise HelperError(
            "identity_mismatch",
            f"{meta.get('version')}-{meta.get('build_id')} != {version}-{build}",
        )
    return target


def poll_health(
    *,
    url: str,
    expected_version: str,
    expected_build_id: str,
    timeout_s: float = 30.0,
    interval_s: float = 0.2,
    require_frame_cycle: bool = True,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_err = "timeout"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            ready = body.get("ready")
            cycle_alive = body.get("frame_cycle_alive")
            blackout = body.get("blackout")
            armed = body.get("output_armed")
            ver = str(body.get("app_version") or body.get("version") or "")
            bid = str(body.get("build_id") or "")
            # Venue Raspberry: armed under blackout is healthy (output_policy=venue_ready).
            policy = str(body.get("output_policy") or "")
            venue_ok = policy == "venue_ready" and blackout is True and armed is True
            desktop_ok = blackout is True and armed is False
            # New candidate: ready === true AND frame_cycle_alive === true.
            # Rollback onto a pre-field venue build may omit the key; False still fails.
            cycle_ok = (
                cycle_alive is True
                if require_frame_cycle
                else (cycle_alive is True or ("frame_cycle_alive" not in body))
            )
            if (
                ready is True
                and cycle_ok
                and (venue_ok or desktop_ok)
                and ver == expected_version
                and bid == expected_build_id
            ):
                return body
            last_err = (
                f"mismatch ready={ready!r} frame_cycle_alive={cycle_alive!r} "
                f"blackout={blackout} armed={armed} "
                f"policy={policy!r} "
                f"version={ver!r}/{expected_version!r} build={bid!r}/{expected_build_id!r}"
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_err = str(exc)
        time.sleep(interval_s)
    raise HelperError("health_timeout", last_err)


def apply_staged_release(
    paths: ManagedPaths,
    *,
    skip_process: bool = False,
    simulate_health_fail: bool = False,
    simulate_crash_after_swap: bool = False,
    simulate_crash_before_swap: bool = False,
    health_url: str | None = None,
    health_timeout_s: float = 30.0,
    on_after_swap: Any | None = None,
    on_after_rollback: Any | None = None,
) -> dict[str, Any]:
    """Swap current←staged, optionally start process, health-check, rollback.

    ``on_after_swap`` / ``on_after_rollback`` are callables invoked after pointer
    writes so the supervisor can (re)start the child before health polling.
    """
    record = load_update_state(paths)
    req_path = apply_request_path(paths)
    req = read_pointer(req_path) or {}
    version = str(req.get("expected_version") or "").strip()
    bid = str(req.get("expected_build_id") or "").strip()
    staged = Path(str(req.get("staged_path") or ""))
    command_id = req.get("command_id") or record.command_id
    if not version or not bid or not str(staged):
        transition(
            record,
            UpdateState.FAILED,
            paths=paths,
            error="identity_required",
            pending_result={
                "command_id": command_id,
                "status": "failed",
                "error": "identity_required",
            },
        )
        if req_path.is_file():
            req_path.unlink(missing_ok=True)
        return {"status": "failed", "error": "identity_required"}

    if simulate_crash_before_swap:
        transition(
            record,
            UpdateState.FAILED,
            paths=paths,
            error="crash_before_swap",
            pending_result={
                "command_id": command_id,
                "status": "failed",
                "error": "crash_before_swap",
                "version": version,
                "build_id": bid,
            },
        )
        if req_path.is_file():
            req_path.unlink(missing_ok=True)
        return {"status": "failed", "error": "crash_before_swap"}

    try:
        staged = assert_release_path_contained(paths, staged)
    except ValueError as exc:
        raise AppUpdateError("bad_staged_path", str(exc)) from exc
    meta_path = staged / "RELEASE.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        transition(
            record,
            UpdateState.FAILED,
            paths=paths,
            error="missing_release_meta",
            pending_result={
                "command_id": command_id,
                "status": "failed",
                "error": "missing_release_meta",
            },
        )
        if req_path.is_file():
            req_path.unlink(missing_ok=True)
        raise AppUpdateError("missing_release_meta", str(exc)) from exc
    if str(meta.get("version") or "") != version or str(meta.get("build_id") or "") != bid:
        transition(
            record,
            UpdateState.FAILED,
            paths=paths,
            error="identity_mismatch",
            pending_result={
                "command_id": command_id,
                "status": "failed",
                "error": "identity_mismatch",
                "version": version,
                "build_id": bid,
                "staged_version": meta.get("version"),
                "staged_build_id": meta.get("build_id"),
            },
        )
        if req_path.is_file():
            req_path.unlink(missing_ok=True)
        return {
            "status": "failed",
            "error": "identity_mismatch",
            "current": read_pointer(paths.current_pointer),
        }

    current = read_pointer(paths.current_pointer)
    if current:
        write_pointer_atomic(paths.previous_pointer, current)

    new_current = {
        "version": version,
        "build_id": bid,
        "path": str(staged),
        "switched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_pointer_atomic(paths.current_pointer, new_current)

    if simulate_crash_after_swap:
        transition(
            record,
            UpdateState.RESTARTING,
            paths=paths,
            error="crash_after_swap",
        )
        return {"status": "crashed_after_swap", "current": new_current}

    if callable(on_after_swap):
        on_after_swap()

    transition(record, UpdateState.HEALTH_CHECK, paths=paths, progress=0.97)

    healthy = False
    health_body: dict[str, Any] | None = None
    if skip_process:
        healthy = not simulate_health_fail
    else:
        url = health_url or os.environ.get(
            "ORNG_HEALTH_URL", "http://127.0.0.1:8000/api/health"
        )
        try:
            health_body = poll_health(
                url=url,
                expected_version=version,
                expected_build_id=bid,
                timeout_s=health_timeout_s,
                require_frame_cycle=True,
            )
            healthy = True
        except HelperError:
            healthy = False

    if req_path.is_file():
        req_path.unlink(missing_ok=True)

    if healthy:
        transition(
            record,
            UpdateState.HEALTHY,
            paths=paths,
            progress=1.0,
            last_successful_version=version,
            last_successful_build_id=bid,
            last_error=None,
            pending_result={
                "command_id": command_id,
                "status": "applied",
                "version": version,
                "build_id": bid,
                "health": health_body,
            },
        )
        return {"status": "applied", "current": new_current}

    return rollback_to_previous(
        paths,
        command_id=str(command_id) if command_id else None,
        skip_process=skip_process,
        simulate_health_fail=False,
        reason="health_failed",
        health_url=health_url,
        health_timeout_s=health_timeout_s,
        on_after_rollback=on_after_rollback or on_after_swap,
        on_after_restore=on_after_rollback or on_after_swap,
    )


def _clear_apply_request(paths: ManagedPaths) -> None:
    req_path = apply_request_path(paths)
    if req_path.is_file():
        req_path.unlink(missing_ok=True)


def _restore_working_release(
    paths: ManagedPaths,
    *,
    restore: dict[str, Any],
    command_id: str | None,
    skip_process: bool,
    health_url: str | None,
    health_timeout_s: float,
    on_after_restore: Any | None,
    error: str,
) -> dict[str, Any]:
    """After unhealthy previous: put working candidate back and report failed."""
    record = load_update_state(paths)
    try:
        validate_pointer_target(paths, restore)
    except HelperError as exc:
        transition(
            record,
            UpdateState.FAILED,
            paths=paths,
            error=f"restore_invalid:{exc.message}",
            pending_result={
                "command_id": command_id,
                "status": "failed",
                "error": f"restore_invalid:{exc.message}",
            },
        )
        _clear_apply_request(paths)
        return {
            "status": "failed",
            "error": f"restore_invalid:{exc.message}",
            "current": read_pointer(paths.current_pointer),
        }

    write_pointer_atomic(paths.current_pointer, restore)
    if callable(on_after_restore):
        on_after_restore()

    restore_ok = True
    if not skip_process:
        url = health_url or os.environ.get(
            "ORNG_HEALTH_URL", "http://127.0.0.1:8000/api/health"
        )
        try:
            poll_health(
                url=url,
                expected_version=str(restore.get("version")),
                expected_build_id=str(restore.get("build_id")),
                timeout_s=health_timeout_s,
                require_frame_cycle=True,
            )
        except HelperError as exc:
            restore_ok = False
            error = f"{error};restore_health_failed:{exc.message}"

    _clear_apply_request(paths)
    transition(
        record,
        UpdateState.FAILED,
        paths=paths,
        progress=1.0,
        last_error=error,
        pending_result={
            "command_id": command_id,
            "status": "failed",
            "error": error,
            # Report the release that is actually running when restore succeeded.
            "version": restore.get("version") if restore_ok else None,
            "build_id": restore.get("build_id") if restore_ok else None,
            "restored": restore_ok,
        },
    )
    return {
        "status": "failed",
        "error": error,
        "restored": restore_ok,
        "current": read_pointer(paths.current_pointer),
    }


def rollback_to_previous(
    paths: ManagedPaths,
    *,
    command_id: str | None = None,
    skip_process: bool = False,
    simulate_health_fail: bool = False,
    reason: str = "rollback",
    health_url: str | None = None,
    health_timeout_s: float = 30.0,
    on_after_rollback: Any | None = None,
    on_after_restore: Any | None = None,
    restore_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Swap current→previous, restart via supervisor hook, health-check.

    On unhealthy previous: restore the pre-rollback (working) pointer, restart it,
    and report ``failed`` with the actually-running version/build. Never raises
    after a pointer swap in a way that leaves the helper dead — returns a status
    dict so the supervisor loop can continue.
    """
    record = load_update_state(paths)
    cid = command_id or record.command_id
    transition(record, UpdateState.ROLLING_BACK, paths=paths)
    previous = read_pointer(paths.previous_pointer)
    if not previous:
        transition(
            record,
            UpdateState.FAILED,
            paths=paths,
            error="no_previous_release",
            pending_result={
                "command_id": cid,
                "status": "failed",
                "error": "no_previous_release",
            },
        )
        _clear_apply_request(paths)
        raise AppUpdateError("no_previous", "No previous release to roll back to")

    try:
        validate_pointer_target(paths, previous)
    except HelperError as exc:
        transition(
            record,
            UpdateState.FAILED,
            paths=paths,
            error=exc.message,
            pending_result={
                "command_id": cid,
                "status": "failed",
                "error": exc.message,
            },
        )
        _clear_apply_request(paths)
        raise AppUpdateError(exc.code, exc.message) from exc

    failed = restore_override or read_pointer(paths.current_pointer)
    # Idempotent: already on previous → just confirm health.
    already = (
        failed
        and str(failed.get("version")) == str(previous.get("version"))
        and str(failed.get("build_id")) == str(previous.get("build_id"))
        and str(failed.get("path")) == str(previous.get("path"))
    )
    if not already:
        write_pointer_atomic(paths.current_pointer, previous)

    if callable(on_after_rollback):
        on_after_rollback()

    prev_ok = True
    health_err = ""
    if not skip_process:
        url = health_url or os.environ.get(
            "ORNG_HEALTH_URL", "http://127.0.0.1:8000/api/health"
        )
        try:
            poll_health(
                url=url,
                expected_version=str(previous.get("version")),
                expected_build_id=str(previous.get("build_id")),
                timeout_s=health_timeout_s,
                require_frame_cycle=False,
            )
        except HelperError as exc:
            prev_ok = False
            health_err = exc.message
    elif simulate_health_fail:
        prev_ok = False
        health_err = "simulated_previous_unhealthy"

    if not prev_ok:
        if failed:
            return _restore_working_release(
                paths,
                restore=failed,
                command_id=str(cid) if cid else None,
                skip_process=skip_process,
                health_url=health_url,
                health_timeout_s=health_timeout_s,
                on_after_restore=on_after_restore or on_after_rollback,
                error=f"previous_unhealthy:{health_err}",
            )
        transition(
            record,
            UpdateState.FAILED,
            paths=paths,
            error=f"previous_unhealthy:{health_err}",
            pending_result={
                "command_id": cid,
                "status": "failed",
                "error": f"previous_unhealthy:{health_err}",
            },
        )
        _clear_apply_request(paths)
        return {
            "status": "failed",
            "error": f"previous_unhealthy:{health_err}",
            "current": read_pointer(paths.current_pointer),
        }

    _clear_apply_request(paths)
    transition(
        record,
        UpdateState.ROLLED_BACK,
        paths=paths,
        progress=1.0,
        rollback_from=(
            f"{failed.get('version')}-{failed.get('build_id')}" if failed else None
        ),
        pending_result={
            "command_id": cid,
            "status": "rolled_back",
            "version": previous.get("version"),
            "build_id": previous.get("build_id"),
            "reason": reason,
        },
    )
    return {"status": "rolled_back", "current": previous}
