#!/usr/bin/env python3
"""Stdlib-only updater helper library (Python ≥3.11).

Copied next to ``updater_helper.py`` under ``<app_root>/bootstrap/``.
Must NOT import ``orng_led`` — production installs have no repo checkout.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

HELPER_PROTOCOL = 2


class HelperError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def app_root_from_env() -> Path:
    env = os.environ.get("ORNG_APP_ROOT", "").strip()
    if env:
        return Path(env)
    # bootstrap/ → app_root
    return Path(__file__).resolve().parent.parent


def assert_release_contained(app_root: Path, target: Path) -> Path:
    releases = (app_root / "releases").resolve()
    resolved = target.resolve()
    try:
        resolved.relative_to(releases)
    except ValueError as exc:
        raise HelperError("path_escape", f"{resolved} not under {releases}") from exc
    if resolved.is_symlink():
        raise HelperError("symlink", str(resolved))
    return resolved


def load_release_meta(path: Path) -> dict[str, Any]:
    meta = path / "RELEASE.json"
    if not meta.is_file():
        raise HelperError("missing_release_meta", str(meta))
    return read_json(meta)


def validate_pointer_target(app_root: Path, pointer: dict[str, Any]) -> Path:
    raw = pointer.get("path")
    if not raw:
        raise HelperError("bad_pointer", "Missing path")
    target = assert_release_contained(app_root, Path(str(raw)))
    if not target.is_dir():
        raise HelperError("missing_release", str(target))
    version = str(pointer.get("version") or "")
    build = str(pointer.get("build_id") or "")
    meta = load_release_meta(target)
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
            # Venue Raspberry keeps output_armed=true under blackout (venue_ready).
            # Desktop/home still require disarmed; venue accepts armed+blackout.
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


def _state_path(app_root: Path) -> Path:
    return app_root / "update-state.json"


def _load_state(app_root: Path) -> dict[str, Any]:
    p = _state_path(app_root)
    if p.is_file():
        try:
            return read_json(p)
        except json.JSONDecodeError:
            return {}
    return {}


def _save_state(app_root: Path, state: dict[str, Any]) -> None:
    write_json_atomic(_state_path(app_root), state)


def apply_staged_release(
    app_root: Path,
    *,
    skip_process: bool = False,
    simulate_health_fail: bool = False,
    simulate_crash_after_swap: bool = False,
    simulate_crash_before_swap: bool = False,
    health_url: str | None = None,
    health_timeout_s: float = 90.0,
    on_after_swap: Callable[[], None] | None = None,
    on_after_rollback: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Swap current←staged, health-check, rollback. Never arms output."""
    app_root = Path(app_root)
    req_path = app_root / "apply-request.json"
    state = _load_state(app_root)
    req = read_json(req_path) if req_path.is_file() else {}
    version = str(req.get("expected_version") or "").strip()
    bid = str(req.get("expected_build_id") or "").strip()
    staged = Path(str(req.get("staged_path") or ""))
    command_id = req.get("command_id") or state.get("command_id")
    if not version or not bid or not str(staged):
        state.update(
            {
                "status": "failed",
                "error": "identity_required",
                "pending_result": {
                    "command_id": command_id,
                    "status": "failed",
                    "error": "identity_required",
                },
            }
        )
        _save_state(app_root, state)
        if req_path.is_file():
            req_path.unlink(missing_ok=True)
        return {"status": "failed", "error": "identity_required"}

    if simulate_crash_before_swap:
        state.update(
            {
                "status": "failed",
                "error": "crash_before_swap",
                "pending_result": {
                    "command_id": command_id,
                    "status": "failed",
                    "error": "crash_before_swap",
                    "version": version,
                    "build_id": bid,
                },
            }
        )
        _save_state(app_root, state)
        if req_path.is_file():
            req_path.unlink(missing_ok=True)
        return {"status": "failed", "error": "crash_before_swap"}

    staged = assert_release_contained(app_root, staged)
    meta = load_release_meta(staged)
    if str(meta.get("version") or "") != version or str(meta.get("build_id") or "") != bid:
        state.update(
            {
                "status": "failed",
                "error": "identity_mismatch",
                "pending_result": {
                    "command_id": command_id,
                    "status": "failed",
                    "error": "identity_mismatch",
                    "version": version,
                    "build_id": bid,
                    "staged_version": meta.get("version"),
                    "staged_build_id": meta.get("build_id"),
                },
            }
        )
        _save_state(app_root, state)
        if req_path.is_file():
            req_path.unlink(missing_ok=True)
        current_ptr = app_root / "current.json"
        current = read_json(current_ptr) if current_ptr.is_file() else None
        return {"status": "failed", "error": "identity_mismatch", "current": current}

    current_ptr = app_root / "current.json"
    previous_ptr = app_root / "previous.json"
    current = read_json(current_ptr) if current_ptr.is_file() else None
    if current:
        write_json_atomic(previous_ptr, current)

    new_current = {
        "version": version,
        "build_id": bid,
        "path": str(staged),
        "switched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    write_json_atomic(current_ptr, new_current)

    if simulate_crash_after_swap:
        state["status"] = "restarting"
        state["error"] = "crash_after_swap"
        _save_state(app_root, state)
        return {"status": "crashed_after_swap", "current": new_current}

    if callable(on_after_swap):
        on_after_swap()

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
        state.update(
            {
                "status": "healthy",
                "state": "healthy",
                "last_successful_version": version,
                "last_successful_build_id": bid,
                "last_error": None,
                "pending_result": {
                    "command_id": command_id,
                    "status": "applied",
                    "version": version,
                    "build_id": bid,
                    "health": health_body,
                },
            }
        )
        _save_state(app_root, state)
        return {"status": "applied", "current": new_current}

    return rollback_to_previous(
        app_root,
        command_id=str(command_id) if command_id else None,
        skip_process=skip_process,
        reason="health_failed",
        health_url=health_url,
        health_timeout_s=health_timeout_s,
        on_after_rollback=on_after_rollback or on_after_swap,
        on_after_restore=on_after_rollback or on_after_swap,
    )


def _clear_request(app_root: Path) -> None:
    req_path = app_root / "apply-request.json"
    if req_path.is_file():
        req_path.unlink(missing_ok=True)


def _restore_working_release(
    app_root: Path,
    *,
    restore: dict[str, Any],
    command_id: str | None,
    skip_process: bool,
    health_url: str | None,
    health_timeout_s: float,
    on_after_restore: Callable[[], None] | None,
    error: str,
) -> dict[str, Any]:
    """Previous unhealthy → put working candidate back; helper stays alive."""
    state = _load_state(app_root)
    current_ptr = app_root / "current.json"
    try:
        validate_pointer_target(app_root, restore)
    except HelperError as exc:
        state.update(
            {
                "status": "failed",
                "state": "failed",
                "error": f"restore_invalid:{exc.message}",
                "pending_result": {
                    "command_id": command_id,
                    "status": "failed",
                    "error": f"restore_invalid:{exc.message}",
                },
            }
        )
        _save_state(app_root, state)
        _clear_request(app_root)
        return {
            "status": "failed",
            "error": f"restore_invalid:{exc.message}",
            "current": read_json(current_ptr) if current_ptr.is_file() else None,
        }

    write_json_atomic(current_ptr, restore)
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

    _clear_request(app_root)
    state.update(
        {
            "status": "failed",
            "state": "failed",
            "error": error,
            "pending_result": {
                "command_id": command_id,
                "status": "failed",
                "error": error,
                "version": restore.get("version") if restore_ok else None,
                "build_id": restore.get("build_id") if restore_ok else None,
                "restored": restore_ok,
            },
        }
    )
    _save_state(app_root, state)
    return {
        "status": "failed",
        "error": error,
        "restored": restore_ok,
        "current": read_json(current_ptr) if current_ptr.is_file() else None,
    }


def rollback_to_previous(
    app_root: Path,
    *,
    command_id: str | None = None,
    skip_process: bool = False,
    reason: str = "rollback",
    health_url: str | None = None,
    health_timeout_s: float = 30.0,
    on_after_rollback: Callable[[], None] | None = None,
    on_after_restore: Callable[[], None] | None = None,
    restore_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    app_root = Path(app_root)
    state = _load_state(app_root)
    previous_ptr = app_root / "previous.json"
    current_ptr = app_root / "current.json"
    req = read_json(app_root / "apply-request.json") if (app_root / "apply-request.json").is_file() else {}
    cid = command_id or req.get("command_id") or state.get("command_id")

    if not previous_ptr.is_file():
        state.update(
            {
                "status": "failed",
                "state": "failed",
                "error": "no_previous_release",
                "pending_result": {
                    "command_id": cid,
                    "status": "failed",
                    "error": "no_previous_release",
                },
            }
        )
        _save_state(app_root, state)
        _clear_request(app_root)
        raise HelperError("no_previous", "No previous release to roll back to")

    previous = read_json(previous_ptr)
    validate_pointer_target(app_root, previous)

    if restore_override:
        failed = restore_override
    elif req.get("restore_path"):
        failed = {
            "version": req.get("restore_version"),
            "build_id": req.get("restore_build_id"),
            "path": req.get("restore_path"),
        }
    else:
        failed = read_json(current_ptr) if current_ptr.is_file() else None

    already = (
        failed
        and str(failed.get("version")) == str(previous.get("version"))
        and str(failed.get("build_id")) == str(previous.get("build_id"))
        and str(failed.get("path")) == str(previous.get("path"))
    )
    if not already:
        write_json_atomic(current_ptr, previous)

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

    if not prev_ok:
        if failed and failed.get("path"):
            return _restore_working_release(
                app_root,
                restore=failed,
                command_id=str(cid) if cid else None,
                skip_process=skip_process,
                health_url=health_url,
                health_timeout_s=health_timeout_s,
                on_after_restore=on_after_restore or on_after_rollback,
                error=f"previous_unhealthy:{health_err}",
            )
        state.update(
            {
                "status": "failed",
                "state": "failed",
                "error": f"previous_unhealthy:{health_err}",
                "pending_result": {
                    "command_id": cid,
                    "status": "failed",
                    "error": f"previous_unhealthy:{health_err}",
                },
            }
        )
        _save_state(app_root, state)
        _clear_request(app_root)
        return {
            "status": "failed",
            "error": f"previous_unhealthy:{health_err}",
            "current": read_json(current_ptr) if current_ptr.is_file() else None,
        }

    _clear_request(app_root)
    state.update(
        {
            "status": "rolled_back",
            "state": "rolled_back",
            "rollback_from": (
                f"{failed.get('version')}-{failed.get('build_id')}" if failed else None
            ),
            "pending_result": {
                "command_id": cid,
                "status": "rolled_back",
                "version": previous.get("version"),
                "build_id": previous.get("build_id"),
                "reason": reason,
            },
        }
    )
    _save_state(app_root, state)
    return {"status": "rolled_back", "current": previous}


def recover_after_crash(app_root: Path) -> dict[str, Any]:
    """Best-effort finalize: leave blackout/disarmed to the release process."""
    app_root = Path(app_root)
    state = _load_state(app_root)
    status = str(state.get("status") or state.get("state") or "")
    req_path = app_root / "apply-request.json"
    if req_path.is_file():
        try:
            req = read_json(req_path)
        except json.JSONDecodeError:
            req = {}
        action = str(req.get("action") or "apply")
        if action == "rollback":
            try:
                return rollback_to_previous(
                    app_root,
                    command_id=str(req.get("command_id") or "") or None,
                    skip_process=True,
                    reason="crash_recovery",
                )
            except HelperError as exc:
                return {"status": "failed", "error": exc.message}
        # Incomplete apply request — finish via apply path (may roll back).
        try:
            return apply_staged_release(app_root, skip_process=True)
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "error": str(exc)}
    if status in {"restarting", "applying", "rolling_back"} and (
        app_root / "previous.json"
    ).is_file():
        try:
            return rollback_to_previous(app_root, skip_process=True, reason="crash_recovery")
        except HelperError as exc:
            return {"status": "failed", "error": exc.message}
    return {"status": status or "idle", "action": "none"}
