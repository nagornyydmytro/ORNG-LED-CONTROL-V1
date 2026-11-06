"""orng-app-release-v1 package pack/unpack/validate.

Deterministic gzip (mtime=0, normalized headers). Never includes scenes,
credentials, cloud metadata, .git, .venv, node_modules, __pycache__, .pyc.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import stat
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orng_led.updater.signing import (
    FORMAT,
    UPDATER_PROTOCOL_VERSION,
    AppSignError,
    build_signing_manifest,
    verify_app_signature,
)
from orng_led.version import updater_protocol_version as local_updater_protocol

MAX_PACKAGE_BYTES = 80_000_000
MAX_FILE_BYTES = 64 * 1024 * 1024
# Embedded CPython + site-packages routinely exceeds 5k files on raspberry/arm64.
MAX_FILE_COUNT = 25_000
# Compressed package stays ≤80MB; decoded aarch64 runtime+wheels ≈200–250MB.
MAX_TOTAL_DECODED = 320_000_000

FORBIDDEN_NAME_PARTS = (
    ".git",
    ".venv",
    "node_modules",
    "config/scenes",
    "runtime.yaml",
    "device.token",
    "cloud-metadata",
    ".pairing",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)

_NTFS_RESERVED = re.compile(
    r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)", re.IGNORECASE
)
_INVALID_WIN_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')


class AppPackageError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_safe_member_name(name: str) -> bool:
    normalized = name.replace("\\", "/").lstrip("/")
    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        return False
    if normalized.endswith("/") or normalized.endswith("."):
        return False
    if ":" in normalized:  # Windows drive / ADS
        return False
    if _INVALID_WIN_CHARS.search(normalized):
        return False
    for part in normalized.split("/"):
        if not part or part in {".", ".."}:
            return False
        if part.endswith(" ") or part.endswith("."):
            return False
        if _NTFS_RESERVED.match(part):
            return False
    lower = normalized.lower()
    if lower.endswith(".pyc") or lower.endswith(".pyo"):
        return False
    for part in FORBIDDEN_NAME_PARTS:
        if part in lower:
            return False
    return True


def _looks_executable(path: Path, data: bytes) -> bool:
    """Unix execute bit is lost when packing on Windows — recover from content/path."""
    rel = path.as_posix().replace("\\", "/")
    name = path.name
    if rel.startswith("runtime/bin/") or "/runtime/bin/" in f"/{rel}":
        return True
    if name in {"run.py", "python", "python3"} or name.startswith("python3."):
        return True
    if name.endswith((".so", ".so.1.0", ".so.1")) or ".so." in name:
        return True
    if data.startswith(b"#!") or data.startswith(b"\x7fELF"):
        return True
    return False


def _file_mode(path: Path, data: bytes | None = None) -> int:
    mode = path.stat().st_mode
    if mode & stat.S_IXUSR:
        return 0o755
    if data is not None and _looks_executable(path, data):
        return 0o755
    return 0o644


def _should_skip(path: Path, rel: str) -> bool:
    if path.name in {"__pycache__", ".pyc"} or path.suffix in {".pyc", ".pyo"}:
        return True
    if any(p.name == "__pycache__" for p in path.parents):
        return True
    lower = rel.lower()
    return any(bad in lower for bad in ("__pycache__", ".pytest_cache", ".mypy_cache"))


def pack_app_directory(
    source_dir: Path,
    *,
    version: str,
    build_id: str,
    platform: str,
    arch: str,
    channel: str = "stable",
    notes: str | None = None,
    sign: bool = True,
    signer: Callable[[dict[str, Any]], tuple[str, str]] | None = None,
    min_updater_version: str | None = None,
    entrypoint: str = "orng_led.main:app",
    launcher: str = "run.py",
    health_endpoint: str = "/api/health",
    runtime: dict[str, Any] | None = None,
) -> tuple[bytes, dict[str, Any], str, str]:
    """Pack directory into signed tar.gz + signing manifest.

    Returns (package_bytes, signing_manifest, signature_b64, key_id).
    ``signer(manifest) -> (signature_b64, key_id)``; default refuses unsigned.
    """
    source = Path(source_dir)
    if not source.is_dir():
        raise AppPackageError("not_dir", f"Not a directory: {source}")

    file_hashes: dict[str, str] = {}
    members: list[tuple[str, bytes, int]] = []
    count = 0
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise AppPackageError("symlink", f"Symlinks not allowed: {path}")
        if not path.is_file():
            continue
        rel = path.relative_to(source).as_posix()
        if _should_skip(path, rel):
            continue
        if not is_safe_member_name(rel):
            raise AppPackageError("path_rejected", f"Forbidden path: {rel}")
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise AppPackageError("file_too_large", rel)
        count += 1
        if count > MAX_FILE_COUNT:
            raise AppPackageError("too_many_files", str(MAX_FILE_COUNT))
        file_hashes[rel] = _sha256(data)
        members.append((rel, data, _file_mode(path, data)))

    if not members:
        raise AppPackageError("empty_archive", "Refuse empty app package")

    # Uncompressed tar with normalized headers, then gzip mtime=0.
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:", format=tarfile.PAX_FORMAT) as tar:
        for rel, data, mode in members:
            info = tarfile.TarInfo(name=rel)
            info.size = len(data)
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mode = mode
            tar.addfile(info, io.BytesIO(data))

    gz_buf = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=gz_buf, mtime=0, compresslevel=9
    ) as gz:
        gz.write(tar_buf.getvalue())
    package = gz_buf.getvalue()

    if len(package) > MAX_PACKAGE_BYTES:
        raise AppPackageError("package_too_large", str(len(package)))

    content_sha256 = _sha256(package)
    manifest = build_signing_manifest(
        version=version,
        build_id=build_id,
        platform=platform,
        arch=arch,
        channel=channel,
        content_sha256=content_sha256,
        package_size=len(package),
        files=file_hashes,
        notes=notes,
        min_updater_version=min_updater_version,
        entrypoint=entrypoint,
        launcher=launcher,
        health_endpoint=health_endpoint,
        runtime=runtime,
    )

    if not sign:
        raise AppSignError("unsigned", "Unsigned app releases are not allowed")
    if signer is None:
        raise AppSignError(
            "no_signer",
            "Provide signer= or use build-app-release with a production/test key",
        )
    signature_b64, key_id = signer(manifest)
    return package, manifest, signature_b64, key_id


def _parse_semver_tuple(v: str) -> tuple[int, ...]:
    core = v.split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    return tuple(int(p) for p in parts[:3])


def unpack_and_verify(
    package: bytes,
    *,
    manifest: dict[str, Any],
    signature_b64: str,
    key_id: str,
    dest: Path,
    expected_platform: str | None = None,
    expected_arch: str | None = None,
) -> dict[str, Any]:
    """Verify signature + exact file set/hashes, extract into dest."""
    if len(package) > MAX_PACKAGE_BYTES:
        raise AppPackageError("package_too_large", str(len(package)))
    if not package:
        raise AppPackageError("empty_archive", "Empty package")
    if manifest.get("format") not in {None, FORMAT} and manifest.get("format") != FORMAT:
        # Accept legacy manifests without format only if schema present; new packs require it.
        if "files" in manifest and manifest.get("format") != FORMAT:
            raise AppPackageError("bad_format", str(manifest.get("format")))
    if manifest.get("format") and manifest.get("format") != FORMAT:
        raise AppPackageError("bad_format", str(manifest.get("format")))
    if _sha256(package) != manifest.get("content_sha256"):
        raise AppPackageError("hash_mismatch", "Package sha256 does not match manifest")
    if int(manifest.get("package_size", -1)) != len(package):
        raise AppPackageError("size_mismatch", "package_size mismatch")
    if expected_platform and manifest.get("platform") != expected_platform:
        raise AppPackageError(
            "platform_mismatch",
            f"Expected {expected_platform}, got {manifest.get('platform')}",
        )
    if expected_arch and manifest.get("arch") != expected_arch:
        raise AppPackageError(
            "arch_mismatch",
            f"Expected {expected_arch}, got {manifest.get('arch')}",
        )

    proto = int(manifest.get("updater_protocol_version") or 0)
    if proto and proto > int(local_updater_protocol()):
        raise AppPackageError(
            "protocol_too_new",
            f"Release requires updater protocol {proto}",
        )
    min_uv = manifest.get("min_updater_version")
    if min_uv:
        from orng_led.version import app_version

        try:
            if _parse_semver_tuple(app_version()) < _parse_semver_tuple(str(min_uv)):
                raise AppPackageError(
                    "min_updater_version",
                    f"Need updater >= {min_uv}, have {app_version()}",
                )
        except AppPackageError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AppPackageError("min_updater_version", str(exc)) from exc

    expected_files = manifest.get("files")
    if not isinstance(expected_files, dict) or not expected_files:
        raise AppPackageError("missing_files", "Manifest must list exact file hashes")

    verify_app_signature(manifest=manifest, signature_b64=signature_b64, key_id=key_id)

    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise AppPackageError("dest_not_empty", str(dest))
    dest.mkdir(parents=True, exist_ok=True)

    total = 0
    written: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as tar:
        members = tar.getmembers()
        if len(members) > MAX_FILE_COUNT:
            raise AppPackageError("too_many_files", str(len(members)))
        seen: set[str] = set()
        for member in members:
            name = member.name.replace("\\", "/")
            if not is_safe_member_name(name):
                raise AppPackageError("path_rejected", name)
            if name in seen:
                raise AppPackageError("duplicate_path", name)
            seen.add(name)
            if member.issym() or member.islnk():
                raise AppPackageError("symlink", name)
            if not member.isfile():
                continue
            if member.size > MAX_FILE_BYTES:
                raise AppPackageError("file_too_large", name)
            total += member.size
            if total > MAX_TOTAL_DECODED:
                raise AppPackageError("decoded_too_large", str(total))
            extracted = tar.extractfile(member)
            if extracted is None:
                raise AppPackageError("corrupt", name)
            data = extracted.read()
            if len(data) != member.size:
                raise AppPackageError("size_mismatch", name)
            target = (dest / name).resolve()
            try:
                target.relative_to(dest.resolve())
            except ValueError as exc:
                raise AppPackageError("path_escape", name) from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            # Preserve executable bit from tar mode; also recover ELF/shebang when
            # the package was built on Windows without Unix +x bits.
            need_exec = bool(member.mode & 0o111) or data.startswith(b"#!") or data.startswith(
                b"\x7fELF"
            )
            if need_exec or name.startswith("runtime/bin/") or name in {"run.py"}:
                target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            written[name] = _sha256(data)

    expected_set = set(expected_files.keys())
    written_set = set(written.keys())
    if written_set != expected_set:
        missing = sorted(expected_set - written_set)
        extra = sorted(written_set - expected_set)
        raise AppPackageError(
            "file_set_mismatch",
            f"missing={missing[:5]} extra={extra[:5]}",
        )
    for name, digest in expected_files.items():
        if written.get(name) != digest:
            raise AppPackageError("file_hash_mismatch", name)

    # Require launcher / RELEASE identity when declared.
    for required in ("RELEASE.json",):
        if required in expected_files and not (dest / required).is_file():
            raise AppPackageError("missing_required", required)

    return written


# Keep protocol constant import used by docs/tests.
_ = UPDATER_PROTOCOL_VERSION
