"""Worker ↔ Python APPLICATION download contract (manifest + package)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from orng_led.updater.download import AppDownloadError, download_assigned_app_release
from orng_led.updater.package import pack_app_directory
from orng_led.updater.paths import ensure_managed_layout
from orng_led.updater.service import AppUpdateService
from orng_led.updater.state import UpdateState, load_update_state
from tests.fixtures.app_signing import TEST_APP_KEY_ID, sign_with_test_key


def _signer(manifest: dict) -> tuple[str, str]:
    return sign_with_test_key(manifest), TEST_APP_KEY_ID


def _build_package(tmp_path: Path):
    src = tmp_path / "payload"
    src.mkdir()
    (src / "hello.txt").write_text("contract", encoding="utf-8")
    (src / "RELEASE.json").write_text(
        json.dumps({"version": "0.2.0", "build_id": "c1"}), encoding="utf-8"
    )
    return pack_app_directory(
        src,
        version="0.2.0",
        build_id="c1",
        platform="desktop_windows",
        arch="amd64",
        signer=_signer,
    )


def test_worker_style_manifest_package_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parser against the real Worker response shape (manifest JSON + gzip package)."""
    monkeypatch.setenv("ORNG_APP_PLATFORM", "desktop_windows")
    monkeypatch.setenv("ORNG_APP_ARCH", "amd64")
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_URL", "https://device.test/ws/device")
    monkeypatch.setattr(
        "orng_led.updater.download.load_device_token",
        lambda: "test-token",
    )
    package, manifest, sig, key_id = _build_package(tmp_path)
    digest = hashlib.sha256(package).hexdigest()
    assignment = {"desired_version": "0.2.0", "desired_build_id": "c1", "auto_stage": True}
    man_body = {
        "manifest": manifest,
        "signature_b64": sig,
        "key_id": key_id,
        "content_sha256": digest,
        "package_size": len(package),
        "package_url": "/device/app/releases/desktop_windows/amd64/0.2.0/c1/package",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/device/app/assignment"):
            return httpx.Response(200, json={"assignment": assignment})
        if path.endswith("/manifest"):
            return httpx.Response(200, json=man_body)
        if path.endswith("/package"):
            return httpx.Response(
                200,
                content=package,
                headers={
                    "content-type": "application/gzip",
                    "content-length": str(len(package)),
                    "x-content-sha256": digest,
                },
            )
        return httpx.Response(404, text="nope")

    async def _run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://device.test"
        ) as client:
            return await download_assigned_app_release(client=client)

    dl = asyncio.run(_run())
    assert dl.signature_b64 == sig
    assert dl.key_id == key_id
    assert dl.content_sha256 == digest
    assert dl.package_size == len(package)
    assert dl.manifest["version"] == "0.2.0"
    assert hashlib.sha256(dl.package_bytes).hexdigest() == digest


def test_stage_via_download_assigned_reaches_verified_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ORNG_APP_ROOT", str(tmp_path / "app"))
    monkeypatch.setenv("ORNG_CONFIG_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("ORNG_INSTALL_MODE", "managed")
    monkeypatch.setenv("ORNG_APP_PLATFORM", "desktop_windows")
    monkeypatch.setenv("ORNG_APP_ARCH", "amd64")
    paths = ensure_managed_layout()
    package, manifest, sig, key_id = _build_package(tmp_path)

    svc = AppUpdateService(None)

    async def fake_download(**_kwargs):
        return package, manifest, sig, key_id

    monkeypatch.setattr(svc, "_download_assigned", fake_download)
    result = asyncio.run(svc.stage(command_id="dl-1"))
    assert result["status"] == "staged"
    assert result["version"] == "0.2.0"
    record = load_update_state(paths)
    assert record.state == UpdateState.STAGED.value
    staged = Path(result["path"])
    assert (staged / "hello.txt").read_text(encoding="utf-8") == "contract"
    assert (staged / "RELEASE.json").is_file()


def test_download_unknown_arch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORNG_APP_ARCH", "unknown")
    monkeypatch.setenv("ORNG_CLOUD_DEVICE_URL", "https://device.test/ws/device")
    monkeypatch.setattr(
        "orng_led.updater.download.load_device_token",
        lambda: "tok",
    )
    with pytest.raises(AppDownloadError) as exc:
        asyncio.run(download_assigned_app_release())
    assert exc.value.code == "unknown_arch"
