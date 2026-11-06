"""Canonical application version and build metadata.

``VERSION`` at the repository root is the single SemVer source.
``build_id`` is computed at runtime (git hash or dirty marker) and is never
written into the same commit that produces it.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

_UPDATER_PROTOCOL_VERSION = 1


def _repo_root() -> Path:
    # backend/orng_led/version.py → repo root
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def app_version() -> str:
    override = os.environ.get("ORNG_APP_VERSION", "").strip()
    if override:
        return override
    version_file = _repo_root() / "VERSION"
    if version_file.is_file():
        text = version_file.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        if text:
            return text
    # Packaged wheel may not include repo VERSION — fall back to package metadata.
    try:
        from importlib.metadata import version

        return version("orng-led-control")
    except Exception:  # noqa: BLE001
        return "0.2.0"


def updater_protocol_version() -> int:
    return int(os.environ.get("ORNG_UPDATER_PROTOCOL_VERSION", _UPDATER_PROTOCOL_VERSION))


@lru_cache(maxsize=1)
def build_id() -> str:
    """git commit hash, or ``dirty-<hash>``, or ``unknown``."""
    override = os.environ.get("ORNG_BUILD_ID", "").strip()
    if override:
        return override
    root = _repo_root()
    if not (root / ".git").exists():
        return os.environ.get("ORNG_RELEASE_BUILD_ID", "unknown")
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        if dirty:
            return f"dirty-{head}" if head else "dirty"
        return head or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def version_payload() -> dict[str, object]:
    return {
        "app_version": app_version(),
        "build_id": build_id(),
        "updater_protocol_version": updater_protocol_version(),
    }
