"""Apply identity is the child process, not an ACK.

Temporary managed root only. The health server is a child of this test and
reads RELEASE.json from the directory named by current.json.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from orng_led.updater.errors import AppUpdateError
from orng_led.updater.helper_protocol import apply_staged_release, rollback_to_previous
from orng_led.updater.package import pack_app_directory
from orng_led.updater.paths import (
    apply_request_path,
    ensure_managed_layout,
    read_pointer,
    write_pointer_atomic,
)
from orng_led.updater.service import AppUpdateService
from orng_led.updater.signing import AppSignError, verify_app_signature
from orng_led.updater.state import UpdateState, load_update_state, transition
from tests.fixtures.app_signing import TEST_APP_KEY_ID, sign_with_test_key

_CHILD = r"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

release = Path(sys.argv[1])
port = int(sys.argv[2])
meta = json.loads((release / "RELEASE.json").read_text(encoding="utf-8"))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/health":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(
            {
                "ready": True,
                "frame_cycle_alive": True,
                "blackout": True,
                "output_armed": False,
                "version": meta["version"],
                "build_id": meta["build_id"],
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: A003
        return


httpd = None
for _ in range(80):
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        break
    except OSError:
        time.sleep(0.05)
if httpd is None:
    raise SystemExit("bind failed")
httpd.serve_forever()
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _release(root: Path, version: str, build: str) -> Path:
    path = root / f"{version}-{build}"
    path.mkdir(parents=True)
    (path / "RELEASE.json").write_text(
        json.dumps({"version": version, "build_id": build, "marker": build}) + "\n",
        encoding="utf-8",
    )
    (path / "payload.txt").write_text(build, encoding="utf-8")
    return path


class _Child:
    def __init__(self, paths, script: Path, port: int) -> None:
        self.paths = paths
        self.script = script
        self.port = port
        self.proc: subprocess.Popen[bytes] | None = None

    def restart(self) -> None:
        self.stop()
        pointer = read_pointer(self.paths.current_pointer)
        assert pointer and pointer.get("path")
        self.proc = subprocess.Popen(
            [sys.executable, str(self.script), str(pointer["path"]), str(self.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def build_id(self) -> str:
        url = f"http://127.0.0.1:{self.port}/api/health"
        last = "no response"
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                return str(body["build_id"])
            except Exception as exc:  # noqa: BLE001
                last = str(exc)
                time.sleep(0.05)
        raise AssertionError(last)


def _signer(manifest: dict) -> tuple[str, str]:
    return sign_with_test_key(manifest), TEST_APP_KEY_ID


@pytest.fixture()
def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ORNG_APP_ROOT", str(tmp_path / "app"))
    monkeypatch.setenv("ORNG_CONFIG_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("ORNG_INSTALL_MODE", "managed")
    monkeypatch.setenv("ORNG_APP_PLATFORM", "desktop_windows")
    monkeypatch.setenv("ORNG_APP_ARCH", "amd64")
    return ensure_managed_layout()


def _stage_record(paths, staged: Path, version: str, build: str, command: str) -> None:
    record = load_update_state(paths)
    transition(
        record,
        UpdateState.STAGED,
        paths=paths,
        staged_version=version,
        staged_build_id=build,
        staged_path=str(staged),
        command_id=command,
        desired_version=version,
        desired_build_id=build,
    )


def test_child_reports_applied_build_then_rollback_build(managed, tmp_path: Path) -> None:
    paths = managed
    old = _release(paths.releases, "0.1.0", "build-a")
    new = _release(paths.releases, "0.2.0", "build-b")
    write_pointer_atomic(
        paths.current_pointer,
        {"version": "0.1.0", "build_id": "build-a", "path": str(old)},
    )
    scene = paths.scenes / "show.json"
    scene.write_text('{"name":"keep"}\n', encoding="utf-8")
    creds = paths.data_root / "credentials" / "device.json"
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text('{"token":"keep"}\n', encoding="utf-8")
    script = tmp_path / "health_child.py"
    script.write_text(_CHILD, encoding="utf-8")
    child = _Child(paths, script, _free_port())
    try:
        child.restart()
        assert child.build_id() == "build-a"

        _stage_record(paths, new, "0.2.0", "build-b", "cmd-b")
        write_pointer_atomic(
            apply_request_path(paths),
            {
                "command_id": "cmd-b",
                "expected_version": "0.2.0",
                "expected_build_id": "build-b",
                "staged_path": str(new),
            },
        )
        applied = apply_staged_release(
            paths,
            health_url=f"http://127.0.0.1:{child.port}/api/health",
            health_timeout_s=8,
            on_after_swap=child.restart,
            on_after_rollback=child.restart,
        )
        assert applied["status"] == "applied"
        assert child.build_id() == "build-b"
        assert (old / "payload.txt").read_text(encoding="utf-8") == "build-a"
        assert scene.read_text(encoding="utf-8") == '{"name":"keep"}\n'
        assert creds.read_text(encoding="utf-8") == '{"token":"keep"}\n'

        rolled = rollback_to_previous(
            paths,
            command_id="cmd-back",
            health_url=f"http://127.0.0.1:{child.port}/api/health",
            health_timeout_s=8,
            on_after_rollback=child.restart,
        )
        assert rolled["status"] == "rolled_back"
        assert child.build_id() == "build-a"
        assert read_pointer(paths.current_pointer)["build_id"] == "build-a"
        assert (new / "payload.txt").read_text(encoding="utf-8") == "build-b"
        assert scene.read_text(encoding="utf-8") == '{"name":"keep"}\n'
    finally:
        child.stop()


def test_expected_b_does_not_install_staged_a(managed, tmp_path: Path) -> None:
    paths = managed
    old = _release(paths.releases, "0.1.0", "build-a")
    write_pointer_atomic(
        paths.current_pointer,
        {"version": "0.1.0", "build_id": "build-a", "path": str(old)},
    )
    _stage_record(paths, old, "0.1.0", "build-a", "cmd-check-b")
    write_pointer_atomic(
        apply_request_path(paths),
        {
            "command_id": "cmd-check-b",
            "expected_version": "0.2.0",
            "expected_build_id": "build-b",
            "staged_path": str(old),
        },
    )
    script = tmp_path / "health_child.py"
    script.write_text(_CHILD, encoding="utf-8")
    child = _Child(paths, script, _free_port())
    try:
        child.restart()
        assert child.build_id() == "build-a"
        out = apply_staged_release(
            paths,
            health_url=f"http://127.0.0.1:{child.port}/api/health",
            on_after_swap=child.restart,
        )
        assert out["status"] == "failed"
        assert out["error"] == "identity_mismatch"
        assert read_pointer(paths.current_pointer)["build_id"] == "build-a"
        assert child.build_id() == "build-a"
        assert not apply_request_path(paths).is_file()
    finally:
        child.stop()


def test_missing_expected_identity_does_not_fall_back_to_staged(managed) -> None:
    paths = managed
    old = _release(paths.releases, "0.1.0", "build-a")
    write_pointer_atomic(
        paths.current_pointer,
        {"version": "0.1.0", "build_id": "build-a", "path": str(old)},
    )
    _stage_record(paths, old, "0.1.0", "build-a", "cmd-blank")
    write_pointer_atomic(
        apply_request_path(paths),
        {"command_id": "cmd-blank", "staged_path": str(old)},
    )
    out = apply_staged_release(paths, skip_process=True)
    assert out["status"] == "failed"
    assert out["error"] == "identity_required"
    assert read_pointer(paths.current_pointer)["build_id"] == "build-a"


class _Output:
    def __init__(self) -> None:
        self.armed: object = False
        self.calls: list[str] = []

    def disarm(self) -> None:
        self.calls.append("disarm")
        self.armed = False

    def _emit_zero_frames(self, count: int = 3, raise_on_error: bool = False) -> None:
        self.calls.append("zero")


class _Engine:
    def __init__(self) -> None:
        self.overlays = self
        self.blackout = False
        self.calls: list[str] = []

    def set_blackout(self, enabled: bool) -> None:
        self.calls.append("blackout")
        self.blackout = enabled


class _Runtime:
    def __init__(self) -> None:
        self.output = _Output()
        self.engine = _Engine()
        self.input_dispatcher = None


def test_apply_mismatch_and_unknown_output_do_not_blackout(managed) -> None:
    paths = managed
    old = _release(paths.releases, "0.1.0", "build-a")
    write_pointer_atomic(
        paths.current_pointer,
        {"version": "0.1.0", "build_id": "build-a", "path": str(old)},
    )
    record = load_update_state(paths)
    transition(
        record,
        UpdateState.STAGED,
        paths=paths,
        staged_version="0.1.0",
        staged_build_id="build-a",
        staged_path=str(old),
        desired_version="0.2.0",
        desired_build_id="build-b",
    )
    runtime = _Runtime()
    svc = AppUpdateService(runtime)
    with pytest.raises(AppUpdateError) as mismatch:
        svc.apply(expected_version="0.2.0", expected_build_id="build-b", simulate=True)
    assert mismatch.value.code == "identity_mismatch"
    assert runtime.engine.calls == []
    assert runtime.output.calls == []
    assert read_pointer(paths.current_pointer)["build_id"] == "build-a"

    record = load_update_state(paths)
    transition(
        record,
        UpdateState.STAGED,
        paths=paths,
        staged_version="0.1.0",
        staged_build_id="build-a",
        staged_path=str(old),
        desired_version="0.1.0",
        desired_build_id="build-a",
        last_error=None,
    )
    runtime.output.armed = "unknown"
    with pytest.raises(AppUpdateError) as unknown:
        svc.apply(expected_version="0.1.0", expected_build_id="build-a", simulate=True)
    assert unknown.value.code == "output_state_unknown"
    assert runtime.engine.calls == []

    runtime.output.armed = True
    waiting = svc.apply(expected_version="0.1.0", expected_build_id="build-a", simulate=True)
    assert waiting["status"] == "waiting_for_safe_state"
    assert runtime.engine.calls == []
    assert read_pointer(paths.current_pointer)["build_id"] == "build-a"


def test_two_applies_install_one_release(managed) -> None:
    paths = managed
    old = _release(paths.releases, "0.1.0", "build-a")
    new = _release(paths.releases, "0.2.0", "build-b")
    write_pointer_atomic(
        paths.current_pointer,
        {"version": "0.1.0", "build_id": "build-a", "path": str(old)},
    )
    _stage_record(paths, new, "0.2.0", "build-b", "cmd-race")
    scene = paths.scenes / "show.json"
    scene.write_text("scene\n", encoding="utf-8")
    svc = AppUpdateService(None)
    results: list[dict] = []
    errors: list[str] = []
    gate = threading.Barrier(2)

    def once() -> None:
        gate.wait(timeout=5)
        try:
            results.append(
                svc.apply(
                    expected_version="0.2.0",
                    expected_build_id="build-b",
                    command_id="cmd-race",
                    simulate=True,
                )
            )
        except AppUpdateError as exc:
            errors.append(exc.code)

    threads = [threading.Thread(target=once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert [item["status"] for item in results] == ["applied"]
    assert errors == ["not_staged"]
    assert read_pointer(paths.current_pointer)["build_id"] == "build-b"
    assert read_pointer(paths.previous_pointer)["build_id"] == "build-a"
    assert (old / "payload.txt").read_text(encoding="utf-8") == "build-a"
    assert scene.read_text(encoding="utf-8") == "scene\n"


def test_power_loss_after_swap_is_not_applied(managed, tmp_path: Path) -> None:
    paths = managed
    old = _release(paths.releases, "0.1.0", "build-a")
    new = _release(paths.releases, "0.2.0", "build-b")
    write_pointer_atomic(
        paths.current_pointer,
        {"version": "0.1.0", "build_id": "build-a", "path": str(old)},
    )
    scene = paths.scenes / "show.json"
    scene.write_text("scene\n", encoding="utf-8")
    _stage_record(paths, new, "0.2.0", "build-b", "cmd-power")
    write_pointer_atomic(
        apply_request_path(paths),
        {
            "command_id": "cmd-power",
            "expected_version": "0.2.0",
            "expected_build_id": "build-b",
            "staged_path": str(new),
        },
    )
    script = tmp_path / "health_child.py"
    script.write_text(_CHILD, encoding="utf-8")
    child = _Child(paths, script, _free_port())
    try:
        child.restart()
        crashed = apply_staged_release(
            paths,
            simulate_crash_after_swap=True,
            on_after_swap=child.restart,
        )
        assert crashed["status"] == "crashed_after_swap"
        assert child.build_id() == "build-a"
        pending = AppUpdateService(None).peek_pending_result()
        assert not pending or pending.get("status") != "applied"
        apply_request_path(paths).unlink(missing_ok=True)
        recovered = AppUpdateService(None).recover_after_crash()
        assert recovered["status"] == "rolled_back"
        assert read_pointer(paths.current_pointer)["build_id"] == "build-a"
        assert child.build_id() == "build-a"
        assert scene.read_text(encoding="utf-8") == "scene\n"
        assert (old / "payload.txt").read_text(encoding="utf-8") == "build-a"
    finally:
        child.stop()


def _pack(tmp_path: Path, build: str) -> tuple[bytes, dict, str, str]:
    src = tmp_path / f"src-{build}"
    src.mkdir()
    (src / "a.txt").write_text(build, encoding="utf-8")
    (src / "RELEASE.json").write_text(
        json.dumps(
            {
                "version": "0.3.0",
                "build_id": build,
                "platform": "desktop_windows",
                "arch": "amd64",
                "channel": "stable",
                "entrypoint": "orng_led.main:app",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return pack_app_directory(
        src,
        version="0.3.0",
        build_id=build,
        platform="desktop_windows",
        arch="amd64",
        signer=_signer,
    )


def test_corrupt_package_disk_full_revoked_key_and_cancel_keep_current(
    managed, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = managed
    current = _release(paths.releases, "0.1.0", "build-a")
    write_pointer_atomic(
        paths.current_pointer,
        {"version": "0.1.0", "build_id": "build-a", "path": str(current)},
    )
    scene = paths.scenes / "show.json"
    scene.write_text("scene\n", encoding="utf-8")
    package, manifest, sig, key_id = _pack(tmp_path, "build-c")
    svc = AppUpdateService(None)

    flipped = bytearray(package)
    flipped[len(flipped) // 2] ^= 0xFF
    with pytest.raises(AppUpdateError):
        import asyncio

        asyncio.run(
            svc.stage(
                package_bytes=bytes(flipped),
                manifest=manifest,
                signature_b64=sig,
                key_id=key_id,
            )
        )
    assert read_pointer(paths.current_pointer)["build_id"] == "build-a"

    with pytest.raises(AppSignError) as revoked:
        verify_app_signature(manifest=manifest, signature_b64=sig, key_id="prod-20261025-01")
    assert revoked.value.code == "unknown_key"
    with pytest.raises(AppUpdateError) as staged_revoked:
        import asyncio

        asyncio.run(
            svc.stage(
                package_bytes=package,
                manifest=manifest,
                signature_b64=sig,
                key_id="prod-20261025-01",
            )
        )
    assert staged_revoked.value.code in {"unknown_key", "verify_failed"}
    assert read_pointer(paths.current_pointer)["build_id"] == "build-a"

    real_replace = os.replace

    def no_space(src, dst) -> None:
        src_s = str(src).replace("\\", "/")
        dst_s = str(dst).replace("\\", "/")
        if "/releases/.staging/" in src_s and "/releases/" in dst_s:
            raise OSError(28, "No space left on device")
        real_replace(src, dst)

    monkeypatch.setattr("orng_led.updater.service.os.replace", no_space)
    with pytest.raises(AppUpdateError) as full:
        import asyncio

        asyncio.run(
            svc.stage(
                package_bytes=package,
                manifest=manifest,
                signature_b64=sig,
                key_id=key_id,
            )
        )
    assert full.value.code in {"verify_failed", "ENOSPC"} or "No space" in str(full.value)
    assert read_pointer(paths.current_pointer)["build_id"] == "build-a"
    assert scene.read_text(encoding="utf-8") == "scene\n"

    record = load_update_state(paths)
    transition(
        record,
        UpdateState.DOWNLOADING,
        paths=paths,
        command_id="cmd-dl",
        desired_version="0.3.0",
        desired_build_id="build-c",
    )
    assert svc.cancel_staged()["status"] == "idle"
    with pytest.raises(AppUpdateError) as late:
        svc.apply(expected_version="0.3.0", expected_build_id="build-c", simulate=True)
    assert late.value.code == "not_staged"
    assert read_pointer(paths.current_pointer)["build_id"] == "build-a"
    assert scene.read_text(encoding="utf-8") == "scene\n"
