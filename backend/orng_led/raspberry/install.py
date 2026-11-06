"""Canonical first-install from signed package + manifest (not a raw tree)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from orng_led.raspberry.runtime_validate import RuntimeValidationError, validate_release_runtime
from orng_led.updater.package import AppPackageError, unpack_and_verify


class InstallError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_sidecar_manifest(manifest_path: Path) -> tuple[dict[str, Any], str, str]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise InstallError("bad_manifest", "manifest root must be object")
    manifest = data.get("manifest")
    if not isinstance(manifest, dict):
        raise InstallError("bad_manifest", "missing manifest object")
    sig = data.get("signature_b64")
    key_id = data.get("key_id")
    if not isinstance(sig, str) or not sig.strip():
        raise InstallError("bad_manifest", "missing signature_b64")
    if not isinstance(key_id, str) or not key_id.strip():
        raise InstallError("bad_manifest", "missing key_id")
    return manifest, sig, key_id


def install_signed_package(
    package_path: Path,
    manifest_path: Path,
    releases_dir: Path,
    *,
    expected_sha256: str | None = None,
    allow_test_key: bool = False,
    allow_dev_artifact: bool = False,
) -> dict[str, Any]:
    """Verify package, unpack safely, validate runtime, return staged path + meta.

    ``allow_test_key`` only enables trust of the test signing public key.
    It does **not** skip ELF / wheelhouse / file-hash checks.
    """
    package_path = Path(package_path)
    manifest_path = Path(manifest_path)
    if not package_path.is_file():
        raise InstallError("missing_package", str(package_path))
    if not manifest_path.is_file():
        raise InstallError("missing_manifest", str(manifest_path))

    raw = package_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and digest.lower() != expected_sha256.lower().strip():
        raise InstallError(
            "sha256_mismatch",
            f"expected {expected_sha256}, got {digest}",
        )
    size = len(raw)
    manifest, sig, key_id = load_sidecar_manifest(manifest_path)

    if allow_test_key:
        os.environ["ORNG_DEV_ALLOW_TEST_APP_KEYS"] = "1"

    releases_dir = Path(releases_dir)
    releases_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="orng-stage-", dir=str(releases_dir)) as tmp:
        stage = Path(tmp) / "tree"
        try:
            unpack_and_verify(
                raw,
                manifest=manifest,
                signature_b64=sig,
                key_id=key_id,
                dest=stage,
                expected_platform="raspberry",
                expected_arch="arm64",
            )
        except AppPackageError as exc:
            raise InstallError(exc.code, exc.message) from exc

        # Runtime checks always fail-closed for wheels + aarch64 ELF on Linux.
        require_elf = True
        try:
            meta = validate_release_runtime(
                stage,
                require_wheels=True,
                require_aarch64_elf=require_elf,
                allow_dev_artifact=allow_dev_artifact or allow_test_key,
            )
        except RuntimeValidationError as exc:
            raise InstallError(exc.code, exc.message) from exc

        version = str(meta.get("version") or manifest.get("version") or "unknown")
        build_id = str(meta.get("build_id") or manifest.get("build_id") or "unknown")
        dest = releases_dir / f"{version}-{build_id}"
        if dest.exists():
            # Idempotent: verified tree already present — keep it.
            return {
                "ok": True,
                "path": str(dest),
                "version": version,
                "build_id": build_id,
                "content_sha256": digest,
                "package_size": size,
                "key_id": key_id,
                "idempotent": True,
                "runtime": meta,
            }
        # Atomic: move staged tree into place.
        shutil.move(str(stage), str(dest))
        return {
            "ok": True,
            "path": str(dest),
            "version": version,
            "build_id": build_id,
            "content_sha256": digest,
            "package_size": size,
            "key_id": key_id,
            "idempotent": False,
            "runtime": meta,
        }
