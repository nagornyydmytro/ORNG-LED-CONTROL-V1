"""Validate managed release runtime / wheelhouse before pointer swap."""

from __future__ import annotations

import json
import re
import struct
import subprocess
from pathlib import Path
from typing import Any

from orng_led.raspberry.contract import load_contract

_PY_VER = re.compile(r"Python\s+(\d+)\.(\d+)")
# ELF e_machine for AArch64
EM_AARCH64 = 183
ELF_MAGIC = b"\x7fELF"


class RuntimeValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def find_release_python(release_dir: Path) -> Path | None:
    candidates = [
        release_dir / "runtime" / "bin" / "python",
        release_dir / "runtime" / "bin" / "python3",
        release_dir / "runtime" / "bin" / "python.exe",
        release_dir / ".venv" / "bin" / "python",
        release_dir / ".venv" / "bin" / "python3",
        release_dir / ".venv" / "Scripts" / "python.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def assert_linux_aarch64_elf(path: Path) -> None:
    """Fail closed unless path is an ELF binary for AArch64 (or a symlink to one)."""
    target = path
    try:
        if path.is_symlink():
            target = path.resolve()
    except OSError as exc:
        raise RuntimeValidationError("elf_resolve_failed", str(exc)) from exc
    try:
        data = target.read_bytes()[:64]
    except OSError as exc:
        raise RuntimeValidationError("elf_read_failed", str(exc)) from exc
    if len(data) < 20 or not data.startswith(ELF_MAGIC):
        # On Windows fixtures we may ship a text stub marked skip_elf.
        raise RuntimeValidationError(
            "not_elf",
            f"{path} is not an ELF binary (need Linux aarch64 runtime)",
        )
    # e_machine at offset 18 (little-endian halfword) for ELF64 little-endian.
    ei_data = data[5]
    ei_class = data[4]
    if ei_class != 2:  # ELFCLASS64
        raise RuntimeValidationError("elf_not_64", f"{path} is not ELF64")
    if ei_data == 1:  # little endian
        machine = struct.unpack_from("<H", data, 18)[0]
    elif ei_data == 2:
        machine = struct.unpack_from(">H", data, 18)[0]
    else:
        raise RuntimeValidationError("elf_endian", f"{path} bad EI_DATA")
    if machine != EM_AARCH64:
        raise RuntimeValidationError(
            "elf_arch_mismatch",
            f"{path} e_machine={machine}, expected AArch64 ({EM_AARCH64})",
        )


def probe_python_version(python: Path) -> tuple[int, int]:
    try:
        out = subprocess.run(
            [str(python), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeValidationError("python_probe_failed", str(exc)) from exc
    text = (out.stdout or out.stderr or "").strip()
    m = _PY_VER.search(text)
    if not m:
        raise RuntimeValidationError("python_version_unparsed", text)
    return int(m.group(1)), int(m.group(2))


def validate_release_runtime(
    release_dir: Path,
    *,
    require_wheels: bool = True,
    require_aarch64_elf: bool = True,
    allow_dev_artifact: bool = False,
) -> dict[str, Any]:
    """Fail closed if release cannot run offline with Python ≥ contract min."""
    contract = load_contract()
    release_dir = Path(release_dir)
    if not release_dir.is_dir():
        raise RuntimeValidationError("missing_release", str(release_dir))
    meta_path = release_dir / "RELEASE.json"
    if not meta_path.is_file():
        raise RuntimeValidationError("missing_release_json", str(meta_path))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("dev_artifact") and not allow_dev_artifact:
        raise RuntimeValidationError(
            "dev_artifact",
            "dev_artifact releases cannot be installed in production",
        )
    if meta.get("platform") and meta.get("platform") != "raspberry":
        raise RuntimeValidationError(
            "platform_mismatch",
            f"expected raspberry, got {meta.get('platform')}",
        )
    if meta.get("arch") and meta.get("arch") != "arm64":
        raise RuntimeValidationError(
            "arch_mismatch",
            f"expected arm64, got {meta.get('arch')}",
        )

    wheelhouse = release_dir / "wheelhouse"
    if not wheelhouse.is_dir():
        raise RuntimeValidationError("missing_wheelhouse", str(wheelhouse))
    wheels = list(wheelhouse.glob("*.whl")) + list(wheelhouse.glob("*.tar.gz"))
    readme_only = (wheelhouse / "README.txt").is_file() and not wheels
    if require_wheels and (not wheels or readme_only):
        raise RuntimeValidationError(
            "empty_wheelhouse",
            "Production raspberry release requires a non-empty arm64 wheelhouse",
        )

    py = find_release_python(release_dir)
    if py is None:
        raise RuntimeValidationError(
            "missing_runtime_python",
            "Release must include runtime/bin/python or .venv/bin/python (≥3.12)",
        )
    # Prefer embedded runtime/ over .venv for Pi managed installs.
    runtime_py = release_dir / "runtime" / "bin" / "python"
    runtime_py3 = release_dir / "runtime" / "bin" / "python3"
    embedded = (
        runtime_py
        if runtime_py.is_file()
        else runtime_py3
        if runtime_py3.is_file()
        else None
    )
    if embedded is None and require_aarch64_elf:
        raise RuntimeValidationError(
            "missing_embedded_runtime",
            "Production raspberry release must ship runtime/bin/python (arm64)",
        )
    if require_aarch64_elf and embedded is not None:
        assert_linux_aarch64_elf(embedded)

    major, minor = probe_python_version(py)
    need = contract.min_release_python
    if (major, minor) < need:
        raise RuntimeValidationError(
            "python_too_old",
            f"Release Python {major}.{minor} < required {need[0]}.{need[1]}",
        )

    runtime_meta = meta.get("runtime") if isinstance(meta.get("runtime"), dict) else {}
    declared = str(runtime_meta.get("python") or "")
    if declared and "3.11" in declared and "3.12" not in declared:
        raise RuntimeValidationError(
            "runtime_contract_stale",
            f"RELEASE.json runtime.python={declared!r} must require ≥3.12",
        )

    if (release_dir / ".git").exists():
        raise RuntimeValidationError("git_checkout", "Refuse git tree as release")

    if require_aarch64_elf and not allow_dev_artifact and meta.get("platform") == "raspberry":
        native = release_dir / "native"
        required_libs = (
            "libportaudio.so.2",
            "libasound.so.2",
            "libjack.so.0",
            "libdb-5.3.so",
        )
        if any(not (native / name).is_file() for name in required_libs):
            raise RuntimeValidationError(
                "missing_portaudio",
                "Production raspberry release must ship the PortAudio library closure",
            )
        site = release_dir / "runtime"
        numpy_dirs = list(site.glob("lib/python*/site-packages/numpy"))
        audio_files = list(site.glob("lib/python*/site-packages/sounddevice.py"))
        audio_dirs = list(site.glob("lib/python*/site-packages/sounddevice"))
        if not numpy_dirs or not (audio_files or audio_dirs):
            raise RuntimeValidationError(
                "missing_audio_python",
                "Production raspberry runtime must already contain numpy and sounddevice",
            )

    return {
        "ok": True,
        "python": str(py),
        "python_version": f"{major}.{minor}",
        "wheelhouse_wheels": len(wheels),
        "wheelhouse_readme_only": readme_only,
        "version": meta.get("version"),
        "build_id": meta.get("build_id"),
        "embedded_runtime": str(embedded) if embedded else None,
    }
