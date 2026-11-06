"""Async + interprocess staging locks; active release preservation."""

from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import os
import time
from pathlib import Path

import pytest

from orng_led.updater.package import pack_app_directory
from orng_led.updater.paths import ensure_managed_layout, read_pointer, write_pointer_atomic
from orng_led.updater.service import AppUpdateService
from tests.fixtures.app_signing import TEST_APP_KEY_ID, sign_with_test_key


def _signer(manifest: dict) -> tuple[str, str]:
    return sign_with_test_key(manifest), TEST_APP_KEY_ID


def _worker_stage(app_root: str, package: bytes, manifest: dict, sig: str, key_id: str) -> str:
    os.environ["ORNG_APP_ROOT"] = app_root
    os.environ["ORNG_INSTALL_MODE"] = "managed"
    os.environ["ORNG_APP_PLATFORM"] = "desktop_windows"
    os.environ["ORNG_APP_ARCH"] = "amd64"
    os.environ["ORNG_DEV_ALLOW_TEST_APP_KEYS"] = "1"
    svc = AppUpdateService(None)
    result = asyncio.run(
        svc.stage(
            package_bytes=package,
            manifest=manifest,
            signature_b64=sig,
            key_id=key_id,
        )
    )
    return result["status"]


def _worker_stage_ordered(
    app_root: str,
    package: bytes,
    manifest: dict,
    sig: str,
    key_id: str,
    role: str,
    flag_dir: str,
) -> dict[str, object]:
    os.environ["ORNG_APP_ROOT"] = app_root
    os.environ["ORNG_INSTALL_MODE"] = "managed"
    os.environ["ORNG_APP_PLATFORM"] = "desktop_windows"
    os.environ["ORNG_APP_ARCH"] = "amd64"
    os.environ["ORNG_DEV_ALLOW_TEST_APP_KEYS"] = "1"
    flags = Path(flag_dir)
    if role == "A":
        os.environ["ORNG_UPDATE_LOCK_ACQUIRED_FLAG"] = str(flags / "A_acquired")
        os.environ["ORNG_UPDATE_LOCK_HOLD_S"] = "0.4"
    else:
        deadline = time.monotonic() + 30.0
        while not (flags / "A_acquired").is_file():
            if time.monotonic() > deadline:
                return {"role": role, "status": "timeout_waiting_A", "t0": 0.0, "t1": 0.0}
            time.sleep(0.02)
        os.environ.pop("ORNG_UPDATE_LOCK_HOLD_S", None)
        os.environ.pop("ORNG_UPDATE_LOCK_ACQUIRED_FLAG", None)
    t0 = time.monotonic()
    svc = AppUpdateService(None)
    result = asyncio.run(
        svc.stage(
            package_bytes=package,
            manifest=manifest,
            signature_b64=sig,
            key_id=key_id,
        )
    )
    t1 = time.monotonic()
    payload = {"role": role, "status": result["status"], "t0": t0, "t1": t1}
    (flags / f"{role}_result.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_two_coroutines_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORNG_APP_ROOT", str(tmp_path / "app"))
    monkeypatch.setenv("ORNG_INSTALL_MODE", "managed")
    monkeypatch.setenv("ORNG_APP_PLATFORM", "desktop_windows")
    monkeypatch.setenv("ORNG_APP_ARCH", "amd64")
    ensure_managed_layout()
    src = tmp_path / "p"
    src.mkdir()
    (src / "a.txt").write_text("x", encoding="utf-8")
    (src / "RELEASE.json").write_text(
        json.dumps(
            {
                "version": "0.2.0",
                "build_id": "lock",
                "platform": "desktop_windows",
                "arch": "amd64",
                "channel": "stable",
                "entrypoint": "orng_led.main:app",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    package, manifest, sig, key_id = pack_app_directory(
        src,
        version="0.2.0",
        build_id="lock",
        platform="desktop_windows",
        arch="amd64",
        signer=_signer,
    )
    svc = AppUpdateService(None)

    async def _run() -> tuple[dict, dict]:
        async def one() -> dict:
            return await svc.stage(
                package_bytes=package, manifest=manifest, signature_b64=sig, key_id=key_id
            )

        return await asyncio.gather(one(), one())

    r1, r2 = asyncio.run(_run())
    assert r1["status"] == "staged"
    assert r2["status"] == "staged"


def test_two_processes_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORNG_APP_ROOT", str(tmp_path / "app"))
    monkeypatch.setenv("ORNG_INSTALL_MODE", "managed")
    monkeypatch.setenv("ORNG_APP_PLATFORM", "desktop_windows")
    monkeypatch.setenv("ORNG_APP_ARCH", "amd64")
    paths = ensure_managed_layout()
    src = tmp_path / "p"
    src.mkdir()
    (src / "a.txt").write_text("x", encoding="utf-8")
    (src / "RELEASE.json").write_text(
        json.dumps(
            {
                "version": "0.2.0",
                "build_id": "proc",
                "platform": "desktop_windows",
                "arch": "amd64",
                "channel": "stable",
                "entrypoint": "orng_led.main:app",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    package, manifest, sig, key_id = pack_app_directory(
        src,
        version="0.2.0",
        build_id="proc",
        platform="desktop_windows",
        arch="amd64",
        signer=_signer,
    )
    flag_dir = tmp_path / "stage-flags"
    flag_dir.mkdir()
    ctx = mp.get_context("spawn")
    args_a = (
        str(paths.app_root),
        package,
        manifest,
        sig,
        key_id,
        "A",
        str(flag_dir),
    )
    args_b = (
        str(paths.app_root),
        package,
        manifest,
        sig,
        key_id,
        "B",
        str(flag_dir),
    )
    proc_a = ctx.Process(target=_worker_stage_ordered, args=args_a)
    proc_b = ctx.Process(target=_worker_stage_ordered, args=args_b)
    proc_a.start()
    deadline = time.monotonic() + 30.0
    while not (flag_dir / "A_acquired").is_file():
        if time.monotonic() > deadline:
            proc_a.terminate()
            pytest.fail("process A never acquired the update lock")
        time.sleep(0.02)
    proc_b.start()
    proc_a.join(timeout=60)
    proc_b.join(timeout=60)
    assert proc_a.exitcode == 0, "process A did not finish staging"
    assert proc_b.exitcode == 0, "process B did not finish staging"
    result_a = json.loads((flag_dir / "A_result.json").read_text(encoding="utf-8"))
    result_b = json.loads((flag_dir / "B_result.json").read_text(encoding="utf-8"))
    assert result_a["status"] == "staged"
    assert result_b["status"] == "staged"
    assert result_b["t0"] < result_a["t1"], "B must start while A still holds the lock"
    assert result_b["t1"] >= result_a["t1"], "B must finish after A released the lock"


def test_cancel_preserves_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORNG_APP_ROOT", str(tmp_path / "app"))
    monkeypatch.setenv("ORNG_INSTALL_MODE", "managed")
    monkeypatch.setenv("ORNG_APP_PLATFORM", "desktop_windows")
    monkeypatch.setenv("ORNG_APP_ARCH", "amd64")
    paths = ensure_managed_layout()
    cur = paths.releases / "0.1.0-cur"
    cur.mkdir(parents=True)
    (cur / "RELEASE.json").write_text(
        json.dumps({"version": "0.1.0", "build_id": "cur"}), encoding="utf-8"
    )
    write_pointer_atomic(
        paths.current_pointer, {"version": "0.1.0", "build_id": "cur", "path": str(cur)}
    )
    src = tmp_path / "p"
    src.mkdir()
    (src / "a.txt").write_text("x", encoding="utf-8")
    (src / "RELEASE.json").write_text(
        json.dumps(
            {
                "version": "0.2.0",
                "build_id": "stg",
                "platform": "desktop_windows",
                "arch": "amd64",
                "channel": "stable",
                "entrypoint": "orng_led.main:app",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    package, manifest, sig, key_id = pack_app_directory(
        src,
        version="0.2.0",
        build_id="stg",
        platform="desktop_windows",
        arch="amd64",
        signer=_signer,
    )
    svc = AppUpdateService(None)
    asyncio.run(
        svc.stage(package_bytes=package, manifest=manifest, signature_b64=sig, key_id=key_id)
    )
    svc.cancel_staged()
    assert cur.is_dir()
    assert read_pointer(paths.current_pointer)["version"] == "0.1.0"
