"""Subprocess helper restart / health / rollback (NOT a real OS restart)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

from orng_led.updater.helper_protocol import apply_staged_release, rollback_to_previous
from orng_led.updater.paths import (
    apply_request_path,
    ensure_managed_layout,
    read_pointer,
    write_pointer_atomic,
)
from orng_led.updater.service import AppUpdateService
from orng_led.updater.state import UpdateState, load_update_state, transition


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _make_release(root: Path, version: str, build: str, *, healthy: bool = True) -> Path:
    path = root / f"{version}-{build}"
    path.mkdir(parents=True, exist_ok=True)
    (path / "RELEASE.json").write_text(
        json.dumps(
            {
                "version": version,
                "build_id": build,
                "health_endpoint": "/api/health",
                "healthy": healthy,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


class _HealthHandler(BaseHTTPRequestHandler):
    version = "0.1.0"
    build_id = "old"
    fail = False

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/api/health":
            self.send_response(404)
            self.end_headers()
            return
        if self.fail:
            self.send_response(500)
            self.end_headers()
            return
        body = json.dumps(
            {
                "status": "ok",
                "ready": True,
                "frame_cycle_alive": True,
                "blackout": True,
                "output_armed": False,
                "version": self.version,
                "build_id": self.build_id,
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def _serve(handler_cls: type[_HealthHandler], port: int) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), handler_cls)
    t = Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


@pytest.fixture()
def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ORNG_APP_ROOT", str(tmp_path / "app"))
    monkeypatch.setenv("ORNG_INSTALL_MODE", "managed")
    paths = ensure_managed_layout()
    return paths


def test_helper_successful_restart(managed, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = managed
    old = _make_release(paths.releases, "0.1.0", "old")
    new = _make_release(paths.releases, "0.2.0", "new")
    write_pointer_atomic(
        paths.current_pointer, {"version": "0.1.0", "build_id": "old", "path": str(old)}
    )
    record = load_update_state(paths)
    transition(
        record,
        UpdateState.STAGED,
        paths=paths,
        staged_version="0.2.0",
        staged_build_id="new",
        staged_path=str(new),
        command_id="cmd-ok",
    )
    write_pointer_atomic(
        apply_request_path(paths),
        {
            "command_id": "cmd-ok",
            "expected_version": "0.2.0",
            "expected_build_id": "new",
            "staged_path": str(new),
        },
    )

    class H(_HealthHandler):
        version = "0.2.0"
        build_id = "new"

    srv = _serve(H, 18765)
    try:
        out = apply_staged_release(
            paths,
            skip_process=False,
            health_url="http://127.0.0.1:18765/api/health",
            health_timeout_s=5.0,
        )
    finally:
        srv.shutdown()
    assert out["status"] == "applied"
    assert read_pointer(paths.current_pointer)["version"] == "0.2.0"
    pending = AppUpdateService(None).peek_pending_result()
    assert pending and pending["status"] == "applied" and pending["command_id"] == "cmd-ok"


def test_helper_health_failure_rollback(managed) -> None:
    paths = managed
    old = _make_release(paths.releases, "0.1.0", "old")
    new = _make_release(paths.releases, "0.2.0", "bad")
    write_pointer_atomic(
        paths.current_pointer, {"version": "0.1.0", "build_id": "old", "path": str(old)}
    )
    write_pointer_atomic(
        paths.previous_pointer, {"version": "0.1.0", "build_id": "old", "path": str(old)}
    )
    record = load_update_state(paths)
    transition(
        record,
        UpdateState.STAGED,
        paths=paths,
        staged_version="0.2.0",
        staged_build_id="bad",
        staged_path=str(new),
        command_id="cmd-bad",
    )
    write_pointer_atomic(
        apply_request_path(paths),
        {
            "command_id": "cmd-bad",
            "expected_version": "0.2.0",
            "expected_build_id": "bad",
            "staged_path": str(new),
        },
    )

    # Health returns wrong version → fail → rollback; then previous health OK.
    class Bad(_HealthHandler):
        version = "0.2.0"
        build_id = "bad"
        fail = True

    # After rollback pointer, poll will still hit same server — switch behavior via mutable.
    state = {"mode": "new"}

    class Switch(_HealthHandler):
        def do_GET(self) -> None:  # noqa: N802
            if state["mode"] == "new":
                self.send_response(500)
                self.end_headers()
                return
            body = json.dumps(
                {
                    "ready": True,
                    "frame_cycle_alive": True,
                    "blackout": True,
                    "output_armed": False,
                    "version": "0.1.0",
                    "build_id": "old",
                }
            ).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    srv = _serve(Switch, 18766)

    def after_rollback() -> None:
        state["mode"] = "old"

    try:
        out = apply_staged_release(
            paths,
            skip_process=False,
            health_url="http://127.0.0.1:18766/api/health",
            health_timeout_s=2.0,
            on_after_rollback=after_rollback,
        )
    finally:
        srv.shutdown()
    assert out["status"] == "rolled_back"
    assert read_pointer(paths.current_pointer)["version"] == "0.1.0"
    pending = AppUpdateService(None).peek_pending_result()
    assert pending and pending["status"] == "rolled_back" and pending["command_id"] == "cmd-bad"


def test_crash_before_and_after_swap(managed) -> None:
    paths = managed
    old = _make_release(paths.releases, "0.1.0", "old")
    new = _make_release(paths.releases, "0.2.0", "new")
    write_pointer_atomic(
        paths.current_pointer, {"version": "0.1.0", "build_id": "old", "path": str(old)}
    )
    record = load_update_state(paths)
    transition(
        record,
        UpdateState.STAGED,
        paths=paths,
        staged_version="0.2.0",
        staged_build_id="new",
        staged_path=str(new),
        command_id="cmd-crash",
    )
    write_pointer_atomic(
        apply_request_path(paths),
        {
            "command_id": "cmd-crash",
            "expected_version": "0.2.0",
            "expected_build_id": "new",
            "staged_path": str(new),
        },
    )
    out = apply_staged_release(paths, skip_process=True, simulate_crash_before_swap=True)
    assert out["status"] == "failed"
    assert read_pointer(paths.current_pointer)["version"] == "0.1.0"

    write_pointer_atomic(
        apply_request_path(paths),
        {
            "command_id": "cmd-crash2",
            "expected_version": "0.2.0",
            "expected_build_id": "new",
            "staged_path": str(new),
        },
    )
    out2 = apply_staged_release(paths, skip_process=True, simulate_crash_after_swap=True)
    assert out2["status"] == "crashed_after_swap"
    assert read_pointer(paths.current_pointer)["version"] == "0.2.0"


def test_previous_unhealthy_restores_candidate(managed) -> None:
    """Unhealthy previous restores the working candidate; helper reports failed."""
    paths = managed
    old = _make_release(paths.releases, "0.1.0", "old")
    cand = _make_release(paths.releases, "0.2.0", "cand")
    write_pointer_atomic(
        paths.previous_pointer, {"version": "0.1.0", "build_id": "old", "path": str(old)}
    )
    write_pointer_atomic(
        paths.current_pointer, {"version": "0.2.0", "build_id": "cand", "path": str(cand)}
    )
    out = rollback_to_previous(
        paths,
        command_id="cmd-prev",
        skip_process=True,
        simulate_health_fail=True,
        restore_override={
            "version": "0.2.0",
            "build_id": "cand",
            "path": str(cand),
        },
    )
    assert out["status"] == "failed"
    assert out.get("restored") is True
    assert read_pointer(paths.current_pointer)["build_id"] == "cand"
    pending = AppUpdateService(None).peek_pending_result()
    assert pending and pending["status"] == "failed"
    assert pending["command_id"] == "cmd-prev"
    assert pending["build_id"] == "cand"
    assert pending.get("restored") is True


def test_apply_once_subprocess(managed) -> None:
    """Real subprocess invocation of bootstrap helper apply-once (simulated health)."""
    paths = managed
    old = _make_release(paths.releases, "0.1.0", "old")
    new = _make_release(paths.releases, "0.2.0", "new")
    write_pointer_atomic(
        paths.current_pointer, {"version": "0.1.0", "build_id": "old", "path": str(old)}
    )
    record = load_update_state(paths)
    transition(
        record,
        UpdateState.STAGED,
        paths=paths,
        staged_version="0.2.0",
        staged_build_id="new",
        staged_path=str(new),
        command_id="cmd-sub",
    )
    write_pointer_atomic(
        apply_request_path(paths),
        {
            "command_id": "cmd-sub",
            "expected_version": "0.2.0",
            "expected_build_id": "new",
            "staged_path": str(new),
        },
    )
    helper = Path(__file__).resolve().parents[2] / "bootstrap" / "updater_helper.py"
    env = os.environ.copy()
    env["ORNG_APP_ROOT"] = str(paths.app_root)
    env["ORNG_INSTALL_MODE"] = "managed"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get(
        "PYTHONPATH", ""
    )
    proc = subprocess.run(
        [sys.executable, str(helper), "apply-once", "--skip-process"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert read_pointer(paths.current_pointer)["version"] == "0.2.0"


def test_service_rollback_defers_to_helper(managed, monkeypatch: pytest.MonkeyPatch) -> None:
    """Production rollback must not swap pointers in-process."""
    paths = managed
    old = _make_release(paths.releases, "0.1.0", "old")
    cand = _make_release(paths.releases, "0.2.0", "cand")
    write_pointer_atomic(
        paths.previous_pointer, {"version": "0.1.0", "build_id": "old", "path": str(old)}
    )
    write_pointer_atomic(
        paths.current_pointer, {"version": "0.2.0", "build_id": "cand", "path": str(cand)}
    )
    # Keep PYTEST_CURRENT_TEST so managed-in-git escape still applies; force
    # non-simulate via explicit simulate=False (production path).
    monkeypatch.delenv("ORNG_UPDATER_SIMULATE", raising=False)
    svc = AppUpdateService(None)
    out = svc.rollback(command_id="cmd-rb-defer", simulate=False)
    assert out["status"] == "restarting"
    assert out.get("action") == "rollback"
    # Pointer still on candidate until helper runs.
    assert read_pointer(paths.current_pointer)["build_id"] == "cand"
    req = read_pointer(apply_request_path(paths))
    assert req and req.get("action") == "rollback"
    assert req.get("command_id") == "cmd-rb-defer"
    assert req.get("expected_build_id") == "old"
    assert req.get("restore_build_id") == "cand"


def test_rollback_idempotent_already_on_previous(managed) -> None:
    paths = managed
    old = _make_release(paths.releases, "0.1.0", "old")
    ptr = {"version": "0.1.0", "build_id": "old", "path": str(old)}
    write_pointer_atomic(paths.current_pointer, ptr)
    write_pointer_atomic(paths.previous_pointer, ptr)
    out1 = rollback_to_previous(paths, command_id="cmd-idem", skip_process=True)
    out2 = rollback_to_previous(paths, command_id="cmd-idem", skip_process=True)
    assert out1["status"] == "rolled_back"
    assert out2["status"] == "rolled_back"
    assert read_pointer(paths.current_pointer)["build_id"] == "old"
    pending = AppUpdateService(None).peek_pending_result()
    assert pending and pending["command_id"] == "cmd-idem"
    assert pending["status"] == "rolled_back"


def test_rollback_interrupt_between_swap_and_restart(managed) -> None:
    """Crash after pointer swap: recover finishes rollback / restore without raising."""
    paths = managed
    old = _make_release(paths.releases, "0.1.0", "old")
    cand = _make_release(paths.releases, "0.2.0", "cand")
    write_pointer_atomic(
        paths.previous_pointer, {"version": "0.1.0", "build_id": "old", "path": str(old)}
    )
    write_pointer_atomic(
        paths.current_pointer, {"version": "0.2.0", "build_id": "cand", "path": str(cand)}
    )
    write_pointer_atomic(
        apply_request_path(paths),
        {
            "action": "rollback",
            "command_id": "cmd-int",
            "expected_version": "0.1.0",
            "expected_build_id": "old",
            "restore_version": "0.2.0",
            "restore_build_id": "cand",
            "restore_path": str(cand),
        },
    )
    # Simulate swap completed, then interruption before health.
    write_pointer_atomic(
        paths.current_pointer, {"version": "0.1.0", "build_id": "old", "path": str(old)}
    )
    record = load_update_state(paths)
    transition(record, UpdateState.RESTARTING, paths=paths, command_id="cmd-int")
    out = AppUpdateService(None).recover_after_crash()
    assert out["status"] in {"rolled_back", "failed"}
    # Request consumed; current is a valid release.
    assert not apply_request_path(paths).is_file()
    cur = read_pointer(paths.current_pointer)
    assert cur and cur["build_id"] in {"old", "cand"}


def _stub_release(
    root: Path,
    version: str,
    build: str,
    *,
    port: int,
    label: str,
    healthy: bool = True,
) -> Path:
    """Release that launches a tiny health HTTP server as the 'runtime'."""
    path = root / f"{version}-{build}"
    path.mkdir(parents=True, exist_ok=True)
    script = path / "stub_server.py"
    status = 200 if healthy else 500
    body_expr = (
        "json.dumps({"
        '"ready": True, "frame_cycle_alive": True, "blackout": True, "output_armed": False, '
        f'"version": {version!r}, "build_id": {build!r}, "label": {label!r}'
        "}).encode()"
        if healthy
        else "b''"
    )
    script.write_text(
        f"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = {port}
STATUS = {status}

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/health":
            self.send_response(404); self.end_headers(); return
        body = {body_expr}
        self.send_response(STATUS)
        if STATUS == 200:
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.end_headers()
    def log_message(self, *a):
        return

class S(HTTPServer):
    allow_reuse_address = True

S(("127.0.0.1", PORT), H).serve_forever()
""",
        encoding="utf-8",
    )
    (path / "RELEASE.json").write_text(
        json.dumps(
            {
                "version": version,
                "build_id": build,
                "health_endpoint": "/api/health",
                "stub_command": [sys.executable, str(script)],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _stop_helper(proc: subprocess.Popen[str]) -> str:
    out = ""
    proc.terminate()
    try:
        out, _ = proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate(timeout=3)
    return out or ""


def test_supervisor_rollback_replaces_process(managed, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real helper `run` loop: rollback request stops child and starts previous."""
    paths = managed
    port = _free_port()
    health = f"http://127.0.0.1:{port}/api/health"
    old = _stub_release(paths.releases, "0.1.0", "old", port=port, label="old")
    cand = _stub_release(paths.releases, "0.2.0", "cand", port=port, label="cand")
    write_pointer_atomic(
        paths.current_pointer, {"version": "0.2.0", "build_id": "cand", "path": str(cand)}
    )
    write_pointer_atomic(
        paths.previous_pointer, {"version": "0.1.0", "build_id": "old", "path": str(old)}
    )
    helper = Path(__file__).resolve().parents[2] / "bootstrap" / "updater_helper.py"
    env = os.environ.copy()
    env["ORNG_APP_ROOT"] = str(paths.app_root)
    env["ORNG_INSTALL_MODE"] = "managed"
    env["ORNG_HELPER_CAPTURE_CHILD"] = "1"
    proc = subprocess.Popen(
        [
            sys.executable,
            str(helper),
            "run",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--health-timeout",
            "8",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    helper_out = ""
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            if proc.poll() is not None:
                helper_out = proc.stdout.read() if proc.stdout else ""
                raise AssertionError(f"helper exited early: {helper_out}")
            try:
                with urllib.request.urlopen(health, timeout=1) as r:
                    body = json.loads(r.read().decode())
                if body.get("build_id") == "cand":
                    break
            except Exception:
                time.sleep(0.2)
        else:
            helper_out = _stop_helper(proc)
            raise AssertionError(f"candidate health never came up: {helper_out}")

        write_pointer_atomic(
            apply_request_path(paths),
            {
                "action": "rollback",
                "command_id": "cmd-sup-rb",
                "expected_version": "0.1.0",
                "expected_build_id": "old",
                "restore_version": "0.2.0",
                "restore_build_id": "cand",
                "restore_path": str(cand),
            },
        )
        deadline = time.time() + 25
        pending: dict = {}
        while time.time() < deadline:
            if proc.poll() is not None:
                helper_out = proc.stdout.read() if proc.stdout else ""
                raise AssertionError(f"helper died during rollback: {helper_out}")
            cur = read_pointer(paths.current_pointer) or {}
            if (
                cur.get("build_id") == "old"
                and not apply_request_path(paths).is_file()
                and paths.update_state.is_file()
            ):
                try:
                    with urllib.request.urlopen(health, timeout=1) as r:
                        body = json.loads(r.read().decode())
                    state = json.loads(paths.update_state.read_text(encoding="utf-8"))
                    pending = state.get("pending_result") or {}
                    if body.get("build_id") == "old" and pending.get("status") == "rolled_back":
                        break
                except Exception:
                    pass
            time.sleep(0.3)
        else:
            helper_out = _stop_helper(proc)
            raise AssertionError(
                f"rollback did not replace process with previous: {helper_out}"
            )
        assert read_pointer(paths.current_pointer)["build_id"] == "old"
        assert proc.poll() is None
        assert pending.get("command_id") == "cmd-sup-rb"
        assert pending.get("status") == "rolled_back"
    finally:
        if proc.poll() is None:
            _stop_helper(proc)


def test_supervisor_unhealthy_previous_restores_candidate(
    managed, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = managed
    port = _free_port()
    health = f"http://127.0.0.1:{port}/api/health"
    old = _stub_release(
        paths.releases, "0.1.0", "old", port=port, label="old", healthy=False
    )
    cand = _stub_release(paths.releases, "0.2.0", "cand", port=port, label="cand")
    write_pointer_atomic(
        paths.current_pointer, {"version": "0.2.0", "build_id": "cand", "path": str(cand)}
    )
    write_pointer_atomic(
        paths.previous_pointer, {"version": "0.1.0", "build_id": "old", "path": str(old)}
    )
    helper = Path(__file__).resolve().parents[2] / "bootstrap" / "updater_helper.py"
    env = os.environ.copy()
    env["ORNG_APP_ROOT"] = str(paths.app_root)
    env["ORNG_INSTALL_MODE"] = "managed"
    proc = subprocess.Popen(
        [
            sys.executable,
            str(helper),
            "run",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--health-timeout",
            "3",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise AssertionError(f"helper exited early: {out}")
            try:
                with urllib.request.urlopen(health, timeout=1) as r:
                    if json.loads(r.read().decode()).get("build_id") == "cand":
                        break
            except Exception:
                time.sleep(0.2)
        else:
            raise AssertionError(f"candidate never healthy: {_stop_helper(proc)}")

        write_pointer_atomic(
            apply_request_path(paths),
            {
                "action": "rollback",
                "command_id": "cmd-sup-restore",
                "expected_version": "0.1.0",
                "expected_build_id": "old",
                "restore_version": "0.2.0",
                "restore_build_id": "cand",
                "restore_path": str(cand),
            },
        )
        deadline = time.time() + 30
        restored = False
        pending: dict = {}
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise AssertionError(f"helper died after failed rollback: {out}")
            cur = read_pointer(paths.current_pointer) or {}
            if cur.get("build_id") == "cand" and not apply_request_path(paths).is_file():
                try:
                    with urllib.request.urlopen(health, timeout=1) as r:
                        body = json.loads(r.read().decode())
                    if body.get("build_id") == "cand" and paths.update_state.is_file():
                        pending = (
                            json.loads(paths.update_state.read_text(encoding="utf-8")).get(
                                "pending_result"
                            )
                            or {}
                        )
                        if pending.get("status") == "failed":
                            restored = True
                            break
                except Exception:
                    pass
            time.sleep(0.3)
        assert restored, f"candidate was not restored after unhealthy previous: {_stop_helper(proc)}"
        assert proc.poll() is None, "helper died after failed rollback"
        assert pending.get("command_id") == "cmd-sup-restore"
        assert pending.get("status") == "failed"
        assert pending.get("build_id") == "cand"
        assert pending.get("restored") is True
    finally:
        if proc.poll() is None:
            _stop_helper(proc)
