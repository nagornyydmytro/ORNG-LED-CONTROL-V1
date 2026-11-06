#!/usr/bin/env python3
"""Stable external updater helper — lives outside replaceable release trees.

Stdlib-only (Python ≥3.11). Does NOT import ``orng_led``.
Copied into ``<app_root>/bootstrap/`` together with ``helper_lib.py``.

Owns the child runtime for ``current.json``. Watches ``apply-request.json``
(action=apply|rollback), stops the child, swaps pointers via helper_lib,
starts the target release, polls ``/api/health`` (ready === true, live frame
cycle, blackout, disarmed, version/build). On failed rollback previous health,
restores the working candidate and keeps the helper alive. Never arms Art-Net.
Never restarts the host OS.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

# Prefer sibling helper_lib (managed install). Fall back only when running
# from a developer checkout where bootstrap/ sits next to helper_lib.py.
_BOOTSTRAP_DIR = Path(__file__).resolve().parent
if str(_BOOTSTRAP_DIR) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_DIR))

import helper_lib  # noqa: E402  — stdlib-only sibling module

HELPER_PROTOCOL = helper_lib.HELPER_PROTOCOL


def app_root() -> Path:
    return helper_lib.app_root_from_env()


def read_json(path: Path) -> dict:
    return helper_lib.read_json(path)


def release_python(release_path: Path) -> str:
    """Only release-local interpreters — never a random global Python for prod."""
    if sys.platform == "win32":
        candidates = [
            release_path / "runtime" / "python.exe",
            release_path / "runtime" / "bin" / "python.exe",
            release_path / ".venv" / "Scripts" / "python.exe",
        ]
    else:
        candidates = [
            release_path / "runtime" / "bin" / "python",
            release_path / "runtime" / "bin" / "python3",
            release_path / ".venv" / "bin" / "python",
            release_path / ".venv" / "bin" / "python3",
        ]
    for c in candidates:
        if c.is_file():
            return str(c)
    raise SystemExit(
        f"No release runtime/.venv python under {release_path} — refusing global fallback"
    )


def _drain_pipe(proc: subprocess.Popen[bytes]) -> None:
    """Continuously drain child stdout to avoid PIPE deadlock; discard bytes."""
    stream = proc.stdout
    if stream is None:
        return
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
    except Exception:  # noqa: BLE001
        return


def launch_release(
    release_path: Path,
    *,
    host: str,
    port: int,
) -> subprocess.Popen[bytes]:
    meta_path = release_path / "RELEASE.json"
    entry = "orng_led.main:app"
    meta: dict = {}
    if meta_path.is_file():
        meta = read_json(meta_path)
        entry = str(meta.get("entrypoint") or entry)
        if meta.get("stub_command"):
            cmd = list(meta["stub_command"])
            env = os.environ.copy()
            env["ORNG_RELEASE_PATH"] = str(release_path)
            env["ORNG_APP_VERSION"] = str(meta.get("version") or "")
            env["ORNG_BUILD_ID"] = str(meta.get("build_id") or "")
            proc = subprocess.Popen(
                cmd,
                cwd=str(release_path),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            threading.Thread(target=_drain_pipe, args=(proc,), daemon=True).start()
            return proc

    python = release_python(release_path)
    env = os.environ.copy()
    env["ORNG_CONFIG_ROOT"] = env.get("ORNG_CONFIG_ROOT") or env.get(
        "ORNG_DATA_ROOT", str(app_root() / ".." / "data")
    )
    env["ORNG_INSTALL_MODE"] = "managed"
    env["ORNG_FRONTEND_DIST"] = str(release_path / "frontend_dist")
    env["ORNG_APP_VERSION"] = str(meta.get("version") or env.get("ORNG_APP_VERSION") or "")
    env["ORNG_BUILD_ID"] = str(meta.get("build_id") or "")
    env["ORNG_RELEASE_BUILD_ID"] = env["ORNG_BUILD_ID"]
    # Release ships its own site-packages under runtime — do not inject host PYTHONPATH.
    env.pop("PYTHONPATH", None)

    cmd = [python, "-m", "uvicorn", entry, "--host", host, "--port", str(port)]
    # Prefer inherit (journald under systemd). PIPE only with drain thread.
    if os.environ.get("ORNG_HELPER_CAPTURE_CHILD") == "1":
        proc = subprocess.Popen(
            cmd,
            cwd=str(release_path),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        threading.Thread(target=_drain_pipe, args=(proc,), daemon=True).start()
        return proc
    return subprocess.Popen(
        cmd,
        cwd=str(release_path),
        env=env,
        stdout=None,
        stderr=None,
    )


def stop_child(proc: subprocess.Popen[bytes] | None, *, timeout_s: float = 15.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    if sys.platform == "win32":
        proc.terminate()
    else:
        proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def cmd_status(_: argparse.Namespace) -> int:
    root = app_root()
    current = root / "current.json"
    print(
        json.dumps(
            {
                "helper_protocol": HELPER_PROTOCOL,
                "app_root": str(root),
                "current": read_json(current) if current.is_file() else None,
                "apply_request": (root / "apply-request.json").is_file(),
                "standalone": True,
            },
            indent=2,
        )
    )
    return 0


def cmd_recover(_: argparse.Namespace) -> int:
    os.environ.setdefault("ORNG_APP_ROOT", str(app_root()))
    out = helper_lib.recover_after_crash(app_root())
    print(json.dumps(out, indent=2))
    return 0


def cmd_apply_once(args: argparse.Namespace) -> int:
    os.environ["ORNG_APP_ROOT"] = str(app_root())
    root = app_root()
    req_path = root / "apply-request.json"
    action = "apply"
    if req_path.is_file():
        try:
            action = str(read_json(req_path).get("action") or "apply")
        except Exception:  # noqa: BLE001
            action = "apply"
    if action == "rollback":
        out = helper_lib.rollback_to_previous(
            root,
            skip_process=args.skip_process,
            health_url=args.health_url,
            health_timeout_s=args.health_timeout,
            reason="admin_rollback",
        )
    else:
        out = helper_lib.apply_staged_release(
            root,
            skip_process=args.skip_process,
            simulate_health_fail=args.health_fail,
            simulate_crash_after_swap=args.crash_after_swap,
            simulate_crash_before_swap=args.crash_before_swap,
            health_url=args.health_url,
            health_timeout_s=args.health_timeout,
        )
    print(json.dumps(out, indent=2))
    return 0 if out.get("status") in {"applied", "rolled_back"} else 1


def cmd_run(args: argparse.Namespace) -> int:
    root = app_root()
    os.environ.setdefault("ORNG_APP_ROOT", str(root))
    helper_lib.recover_after_crash(root)

    child: subprocess.Popen[bytes] | None = None
    health_url = args.health_url or f"http://{args.host}:{args.port}/api/health"
    crash_streak = 0
    stopping = False

    def start_current() -> subprocess.Popen[bytes]:
        current_path = root / "current.json"
        if not current_path.is_file():
            raise SystemExit("current.json missing")
        current = read_json(current_path)
        release_path = Path(current.get("path") or "")
        if not release_path.is_dir():
            raise SystemExit(f"release path missing: {release_path}")
        return launch_release(release_path, host=args.host, port=args.port)

    def _handle_signal(signum: int, _frame: object) -> None:
        nonlocal stopping, child
        stopping = True
        stop_child(child)

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    child = start_current()
    try:
        while not stopping:
            req = root / "apply-request.json"
            if req.is_file() and child is not None:
                time.sleep(0.3)
                stop_child(child)
                child = None
                crash_streak = 0

                def _restart() -> None:
                    nonlocal child
                    stop_child(child)
                    child = start_current()

                try:
                    req_data = read_json(req) if req.is_file() else {}
                except Exception:  # noqa: BLE001
                    req_data = {}
                action = str(req_data.get("action") or "apply")
                try:
                    if action == "rollback":
                        helper_lib.rollback_to_previous(
                            root,
                            command_id=str(req_data.get("command_id") or "") or None,
                            skip_process=False,
                            health_url=health_url,
                            health_timeout_s=args.health_timeout,
                            on_after_rollback=_restart,
                            on_after_restore=_restart,
                            reason="admin_rollback",
                        )
                    else:
                        helper_lib.apply_staged_release(
                            root,
                            skip_process=False,
                            health_url=health_url,
                            health_timeout_s=args.health_timeout,
                            on_after_swap=_restart,
                            on_after_rollback=_restart,
                        )
                except Exception as exc:  # noqa: BLE001
                    # Never crash the supervisor on a bad update/rollback.
                    print(
                        json.dumps(
                            {
                                "error": "request_failed",
                                "action": action,
                                "detail": str(exc),
                                "note": "helper staying up; not auto-arming",
                            }
                        ),
                        flush=True,
                    )
                    try:
                        if child is None:
                            child = start_current()
                    except SystemExit:
                        pass
                    crash_streak = 0
            if child is not None and child.poll() is not None:
                crash_streak += 1
                helper_lib.recover_after_crash(root)
                # Circuit breaker / backoff — never 1s infinite thrash.
                delay = min(60.0, float(2 ** min(crash_streak, 5)))
                time.sleep(delay)
                if crash_streak >= 10:
                    print(
                        json.dumps(
                            {
                                "error": "child_crash_circuit_open",
                                "streak": crash_streak,
                                "note": "helper staying up for diagnostics; not auto-arming",
                            }
                        ),
                        flush=True,
                    )
                    time.sleep(30.0)
                    continue
                try:
                    child = start_current()
                except SystemExit as exc:
                    print(json.dumps({"error": "relaunch_failed", "detail": str(exc)}), flush=True)
                    time.sleep(30.0)
            time.sleep(0.5)
    finally:
        stop_child(child)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ORNG LED CONTROL bootstrap updater helper")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("recover")
    run_p = sub.add_parser("run")
    run_p.add_argument("--host", default="127.0.0.1")
    run_p.add_argument("--port", type=int, default=8000)
    run_p.add_argument("--health-url", default=None)
    run_p.add_argument("--health-timeout", type=float, default=30.0)
    apply_p = sub.add_parser("apply-once")
    apply_p.add_argument("--skip-process", action="store_true")
    apply_p.add_argument("--health-fail", action="store_true")
    apply_p.add_argument("--crash-after-swap", action="store_true")
    apply_p.add_argument("--crash-before-swap", action="store_true")
    apply_p.add_argument("--health-url", default=None)
    apply_p.add_argument("--health-timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "recover":
        return cmd_recover(args)
    if args.cmd == "apply-once":
        return cmd_apply_once(args)
    if args.cmd == "run":
        return cmd_run(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
