"""Updater identity: a staged package is not the desired package, and a check is not an apply.

Uses a temporary managed root and a local child process. Nothing is sent to a venue.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import httpx
import pytest

from orng_led.updater import helper_protocol
from orng_led.updater.paths import (
    apply_request_path,
    ensure_managed_layout,
    read_pointer,
    write_pointer_atomic,
)
from orng_led.updater.service import AppUpdateError, AppUpdateService
from orng_led.updater.state import UpdateState, load_update_state, transition
from tests.test_app_updater import _pack

_CHILD = """
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

pointer = Path(os.environ["ORNG_POINTER"])

def release():
    current = json.loads(pointer.read_text(encoding="utf-8"))
    return json.loads((Path(current["path"]) / "RELEASE.json").read_text(encoding="utf-8"))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        rel = release()
        body = json.dumps({
            "ready": True,
            "frame_cycle_alive": True,
            "blackout": True,
            "output_armed": False,
            "version": rel["version"],
            "build_id": rel["build_id"],
            "pid": os.getpid(),
        }).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, format, *args):
        return

class Server(ThreadingHTTPServer):
    allow_reuse_address = True

port = int(sys.argv[1])
last = None
for _ in range(20):
    try:
        Server(("127.0.0.1", port), Handler).serve_forever()
    except OSError as exc:
        last = exc
        time.sleep(0.1)
raise SystemExit(str(last))
"""


class _Engine:
    def __init__(self) -> None:
        self.blackouts: list[bool] = []
        self.overlays = type("Overlays", (), {"blackout": False})()

    def set_blackout(self, value: bool) -> None:
        self.blackouts.append(bool(value))
        self.overlays.blackout = bool(value)


class _Output:
    def __init__(self, armed: bool = False) -> None:
        self.armed = armed
        self.disarms = 0

    def disarm(self) -> None:
        self.disarms += 1
        self.armed = False

    def _emit_zero_frames(self, count: int = 3, raise_on_error: bool = True) -> None:
        return None


class _UnknownOutput:
    @property
    def armed(self) -> bool:
        raise RuntimeError("output unreadable")

    def disarm(self) -> None:
        raise AssertionError("disarm")

    def _emit_zero_frames(self, count: int = 3, raise_on_error: bool = True) -> None:
        raise AssertionError("frames")


class _Runtime:
    def __init__(self, output: object) -> None:
        self.output = output
        self.engine = _Engine()
        self.input_dispatcher = None


def _managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    app = tmp_path / "app"
    data = tmp_path / "data"
    monkeypatch.setenv("ORNG_APP_ROOT", str(app))
    monkeypatch.setenv("ORNG_CONFIG_ROOT", str(data))
    monkeypatch.setenv("ORNG_INSTALL_MODE", "managed")
    monkeypatch.setenv("ORNG_APP_PLATFORM", "desktop_windows")
    monkeypatch.setenv("ORNG_APP_ARCH", "amd64")
    paths = ensure_managed_layout()
    current = paths.releases / "0.1.0-build-a"
    current.mkdir(parents=True)
    (current / "RELEASE.json").write_text(
        json.dumps({"version": "0.1.0", "build_id": "build-a"}),
        encoding="utf-8",
    )
    (current / "marker.txt").write_text("working-a", encoding="utf-8")
    write_pointer_atomic(
        paths.current_pointer,
        {"version": "0.1.0", "build_id": "build-a", "path": str(current)},
    )
    paths.scenes.mkdir(parents=True, exist_ok=True)
    scene = paths.scenes / "show.yaml"
    scene.write_text("scene-keep\n", encoding="utf-8")
    cred = paths.data_root / "credentials.json"
    cred.write_text('{"token":"keep"}\n', encoding="utf-8")
    return paths, scene.read_bytes(), cred.read_bytes()


def _package(tmp_path: Path, name: str, version: str, build: str, **extra):
    src = tmp_path / name
    src.mkdir()
    (src / "marker.txt").write_text(build, encoding="utf-8")
    return _pack(
        src,
        version=version,
        build_id=build,
        platform=extra.pop("platform", "desktop_windows"),
        arch=extra.pop("arch", "amd64"),
        **extra,
    )


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def test_staged_a_is_not_applied_as_desired_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, scene_bytes, cred_bytes = _managed(tmp_path, monkeypatch)
    package, manifest, sig, key_id = _package(tmp_path, "staged", "0.1.1", "staged-a")
    svc = AppUpdateService(_Runtime(_Output()))
    asyncio.run(
        svc.stage(
            package_bytes=package,
            manifest=manifest,
            signature_b64=sig,
            key_id=key_id,
            command_id="stage-a",
        )
    )
    record = load_update_state(paths)
    transition(
        record,
        UpdateState.STAGED,
        paths=paths,
        desired_version="0.2.0",
        desired_build_id="build-b",
    )
    with pytest.raises(AppUpdateError) as mismatch:
        svc.apply(
            expected_version="0.2.0",
            expected_build_id="build-b",
            simulate=True,
        )
    assert mismatch.value.code == "identity_mismatch"
    assert svc.runtime.engine.blackouts == []
    assert read_pointer(paths.current_pointer)["build_id"] == "build-a"
    with pytest.raises(AppUpdateError) as replaced:
        svc.apply(
            expected_version="0.1.1",
            expected_build_id="staged-a",
            simulate=True,
        )
    assert replaced.value.code == "assignment_replaced"
    assert svc.runtime.engine.blackouts == []
    assert (paths.scenes / "show.yaml").read_bytes() == scene_bytes
    assert (paths.data_root / "credentials.json").read_bytes() == cred_bytes


def test_old_assignment_does_not_replace_a_staged_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, scene_bytes, cred_bytes = _managed(tmp_path, monkeypatch)
    monkeypatch.setenv("ORNG_UPDATER_SIMULATE", "1")
    package, manifest, sig, key_id = _package(tmp_path, "new", "0.2.0", "build-new")
    svc = AppUpdateService(_Runtime(_Output()))
    asyncio.run(
        svc.stage(
            package_bytes=package,
            manifest=manifest,
            signature_b64=sig,
            key_id=key_id,
            command_id="stage-new",
        )
    )
    token = {"value": None}

    monkeypatch.setattr(
        "orng_led.cloud.credentials.load_device_token",
        lambda: token["value"],
    )
    monkeypatch.setattr(
        "orng_led.cloud.sync_service.cloud_http_base",
        lambda: "https://device.test",
    )

    class _Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_exc: object) -> bool:
            return False

        async def get(self, _url: str, headers: dict | None = None) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "assignment": {
                        "desired_version": "0.1.0",
                        "desired_build_id": "build-a",
                        "auto_stage": True,
                    }
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    with pytest.raises(AppUpdateError) as missing:
        asyncio.run(svc.check_assigned())
    assert missing.value.code == "no_token"
    stayed = load_update_state(paths)
    assert stayed.state == UpdateState.STAGED.value
    assert stayed.staged_build_id == "build-new"

    token["value"] = "device-token"
    checked = asyncio.run(svc.check_assigned())
    assert checked["result"] == "checked"
    record = load_update_state(paths)
    assert record.state == UpdateState.STAGED.value
    assert record.desired_build_id == "build-new"
    assert record.staged_build_id == "build-new"
    assert record.staged_content_sha256 == manifest["content_sha256"]

    with pytest.raises(AppUpdateError) as old:
        asyncio.run(
            svc.stage(version="0.1.0", build_id_wanted="build-a", command_id="old-cloud")
        )
    assert old.value.code == "staged_candidate"
    still = load_update_state(paths)
    assert still.state == UpdateState.STAGED.value
    assert still.staged_build_id == "build-new"

    applied = svc.apply(
        expected_version="0.2.0",
        expected_build_id="build-new",
        expected_content_sha256=str(manifest["content_sha256"]),
        force_unsafe=True,
        command_id="stage-new",
        simulate=True,
    )
    assert applied["status"] == "applied"
    current = read_pointer(paths.current_pointer)
    assert current["build_id"] == "build-new"
    assert current["version"] == "0.2.0"
    previous = read_pointer(paths.previous_pointer)
    assert previous["build_id"] == "build-a"
    assert (paths.scenes / "show.yaml").read_bytes() == scene_bytes
    assert (paths.data_root / "credentials.json").read_bytes() == cred_bytes


def test_unknown_output_is_not_disarmed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _, _ = _managed(tmp_path, monkeypatch)
    package, manifest, sig, key_id = _package(tmp_path, "known", "0.2.0", "build-b")
    svc = AppUpdateService(_Runtime(_UnknownOutput()))
    asyncio.run(
        svc.stage(
            package_bytes=package,
            manifest=manifest,
            signature_b64=sig,
            key_id=key_id,
        )
    )
    with pytest.raises(AppUpdateError) as exc:
        svc.apply(
            expected_version="0.2.0",
            expected_build_id="build-b",
            simulate=True,
        )
    assert exc.value.code == "output_state_unknown"
    assert svc.runtime.engine.blackouts == []
    assert read_pointer(paths.current_pointer)["build_id"] == "build-a"


def test_cancel_during_download_does_not_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _, _ = _managed(tmp_path, monkeypatch)
    package, manifest, sig, key_id = _package(tmp_path, "dl", "0.2.0", "build-b")
    svc = AppUpdateService(None)

    async def cancel_mid_download(**_kwargs):
        svc.cancel_staged()
        return package, manifest, sig, key_id

    monkeypatch.setattr(svc, "_download_assigned", cancel_mid_download)
    result = asyncio.run(svc.stage(version="0.2.0", build_id_wanted="build-b", command_id="dl-1"))
    assert result["status"] == "cancelled"
    record = load_update_state(paths)
    assert record.state == UpdateState.IDLE.value
    assert record.staged_build_id is None
    assert read_pointer(paths.current_pointer)["build_id"] == "build-a"


def test_two_applies_swap_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _, _ = _managed(tmp_path, monkeypatch)
    package, manifest, sig, key_id = _package(tmp_path, "once", "0.2.0", "build-b")
    svc = AppUpdateService(None)
    asyncio.run(
        svc.stage(package_bytes=package, manifest=manifest, signature_b64=sig, key_id=key_id)
    )

    def once() -> str:
        try:
            result = svc.apply(
                expected_version="0.2.0",
                expected_build_id="build-b",
                simulate=True,
            )
        except AppUpdateError as exc:
            return exc.code
        return str(result.get("status"))

    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def runner() -> None:
        barrier.wait()
        outcomes.append(once())

    threads = [threading.Thread(target=runner) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert outcomes.count("applied") == 1
    assert "not_staged" in outcomes
    assert read_pointer(paths.current_pointer)["build_id"] == "build-b"
    assert read_pointer(paths.previous_pointer)["build_id"] == "build-a"


def test_bad_packages_keep_the_working_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, scene_bytes, cred_bytes = _managed(tmp_path, monkeypatch)
    svc = AppUpdateService(None)
    cases = []
    corrupt, manifest, sig, key_id = _package(tmp_path, "corrupt", "0.2.0", "corrupt")
    cases.append((corrupt[:-1] + bytes([corrupt[-1] ^ 0xFF]), manifest, sig, key_id))
    mismatch = _package(
        tmp_path, "arch", "0.2.0", "arch", platform="raspberry", arch="arm64"
    )
    cases.append(mismatch)
    helper = _package(tmp_path, "helper", "0.2.0", "helper", min_updater_version="99.0.0")
    cases.append(helper)
    revoked_src = _package(tmp_path, "revoked", "0.2.0", "revoked")
    cases.append((revoked_src[0], revoked_src[1], revoked_src[2], "revoked-key"))
    for package, manifest, sig, key in cases:
        with pytest.raises(AppUpdateError):
            asyncio.run(
                svc.stage(
                    package_bytes=package,
                    manifest=manifest,
                    signature_b64=sig,
                    key_id=key,
                )
            )
        assert read_pointer(paths.current_pointer)["build_id"] == "build-a"
    assert (paths.scenes / "show.yaml").read_bytes() == scene_bytes
    assert (paths.data_root / "credentials.json").read_bytes() == cred_bytes


def test_disk_full_does_not_replace_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _, _ = _managed(tmp_path, monkeypatch)
    package, manifest, sig, key_id = _package(tmp_path, "full", "0.2.0", "build-b")

    real_replace = os.replace

    def no_space(src, dst, *args, **kwargs):
        if ".staging" in str(src):
            raise OSError(28, "No space left on device")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", no_space)
    svc = AppUpdateService(None)
    with pytest.raises(AppUpdateError):
        asyncio.run(
            svc.stage(
                package_bytes=package,
                manifest=manifest,
                signature_b64=sig,
                key_id=key_id,
            )
        )
    assert read_pointer(paths.current_pointer)["build_id"] == "build-a"
    assert load_update_state(paths).state == UpdateState.FAILED.value


def test_power_loss_before_swap_keeps_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, scene_bytes, _ = _managed(tmp_path, monkeypatch)
    record = load_update_state(paths)
    transition(record, UpdateState.DOWNLOADING, paths=paths, command_id="half")
    out = AppUpdateService(None).recover_after_crash()
    assert out["status"] == "failed"
    assert read_pointer(paths.current_pointer)["build_id"] == "build-a"
    record = load_update_state(paths)
    transition(
        record,
        UpdateState.STAGED,
        paths=paths,
        staged_version="0.1.0",
        staged_build_id="build-a",
    )
    held = AppUpdateService(None).recover_after_crash()
    assert held["action"] == "none"
    assert read_pointer(paths.current_pointer)["build_id"] == "build-a"
    assert (paths.scenes / "show.yaml").read_bytes() == scene_bytes


def test_child_process_build_id_after_apply_and_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, scene_bytes, cred_bytes = _managed(tmp_path, monkeypatch)
    package, manifest, sig, key_id = _package(tmp_path, "child", "0.2.0", "build-b")
    runtime = _Runtime(_Output(False))
    svc = AppUpdateService(runtime)
    asyncio.run(
        svc.stage(
            package_bytes=package,
            manifest=manifest,
            signature_b64=sig,
            key_id=key_id,
            command_id="stage-b",
        )
    )
    with pytest.raises(AppUpdateError):
        svc.apply(expected_version="9.9.9", expected_build_id="other", simulate=False)
    assert runtime.engine.blackouts == []
    assert not apply_request_path(paths).is_file()

    accepted = svc.apply(
        expected_version="0.2.0",
        expected_build_id="build-b",
        expected_content_sha256=str(manifest["content_sha256"]),
        simulate=False,
    )
    assert accepted["status"] == "restarting"
    assert read_pointer(paths.current_pointer)["build_id"] == "build-a"

    script = tmp_path / "health_child.py"
    script.write_text(_CHILD, encoding="utf-8")
    port = _free_port()
    url = f"http://127.0.0.1:{port}/api/health"
    holder: dict[str, subprocess.Popen[bytes] | None] = {"proc": None}

    def restart() -> None:
        current = holder["proc"]
        if current is not None and current.poll() is None:
            current.terminate()
            try:
                current.wait(timeout=5)
            except subprocess.TimeoutExpired:
                current.kill()
        env = os.environ.copy()
        env["ORNG_POINTER"] = str(paths.current_pointer)
        holder["proc"] = subprocess.Popen(
            [sys.executable, str(script), str(port)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def health() -> dict:
        deadline = time.time() + 5
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=0.5) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except OSError as exc:
                last_error = exc
                time.sleep(0.05)
        raise AssertionError(last_error)

    try:
        restart()
        before = health()
        assert before["build_id"] == "build-a"
        applied = helper_protocol.apply_staged_release(
            paths,
            health_url=url,
            health_timeout_s=8,
            on_after_swap=restart,
        )
        assert applied["status"] == "applied"
        after = health()
        assert after["build_id"] == "build-b"
        assert after["pid"] != before["pid"]
        rolled = helper_protocol.rollback_to_previous(
            paths,
            health_url=url,
            health_timeout_s=8,
            on_after_rollback=restart,
        )
        assert rolled["status"] == "rolled_back"
        restored = health()
        assert restored["build_id"] == "build-a"
        assert restored["pid"] != after["pid"]
    finally:
        proc = holder["proc"]
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    assert (paths.scenes / "show.yaml").read_bytes() == scene_bytes
    assert (paths.data_root / "credentials.json").read_bytes() == cred_bytes
    assert (paths.releases / "0.1.0-build-a" / "marker.txt").read_text(encoding="utf-8") == (
        "working-a"
    )
