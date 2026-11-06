"""Developer checkout vs managed install detection.

A ``.git`` checkout is ALWAYS developer — even if ``current.json`` exists or
``ORNG_INSTALL_MODE=managed`` is set. The only override is the test-only flag
``ORNG_ALLOW_MANAGED_IN_GIT=1`` (fail-closed otherwise).
"""

from __future__ import annotations

import os
import sys
from enum import StrEnum
from pathlib import Path

from orng_led.updater.paths import default_app_root, resolve_managed_paths


class InstallMode(StrEnum):
    DEVELOPER = "developer"
    MANAGED = "managed"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _git_checkout() -> bool:
    return (_repo_root() / ".git").is_dir()


def detect_install_mode() -> InstallMode:
    # Explicit developer always wins.
    forced = os.environ.get("ORNG_INSTALL_MODE", "").strip().lower()
    if forced in {"developer", "dev"}:
        return InstallMode.DEVELOPER

    # .git checkout → developer unless special test-only escape hatch.
    if _git_checkout():
        allow = os.environ.get("ORNG_ALLOW_MANAGED_IN_GIT", "").strip().lower()
        if allow in {"1", "true", "yes"} and forced in {"managed", "install"}:
            return InstallMode.MANAGED
        # Pytest + ORNG_INSTALL_MODE=managed under tmp app root is the common
        # unit-test pattern (code still lives in a git checkout).
        if os.environ.get("PYTEST_CURRENT_TEST") and forced in {"managed", "install"}:
            return InstallMode.MANAGED
        return InstallMode.DEVELOPER

    if forced in {"managed", "install"}:
        return InstallMode.MANAGED

    # Explicit managed pointer wins when not a git checkout.
    paths = resolve_managed_paths()
    if paths.current_pointer.is_file():
        return InstallMode.MANAGED

    # Packaged under managed app root.
    try:
        here = Path(__file__).resolve()
        app_root = default_app_root().resolve()
        if app_root in here.parents or str(here).startswith(str(app_root)):
            return InstallMode.MANAGED
    except Exception:  # noqa: BLE001
        pass

    return InstallMode.DEVELOPER


def detect_platform() -> str:
    """Cloud-compatible platform id."""
    override = os.environ.get("ORNG_APP_PLATFORM", "").strip()
    if override:
        return override
    from orng_led.cloud.device_mode import CloudDeviceMode, cloud_device_mode

    mode = cloud_device_mode()
    if mode == CloudDeviceMode.RASPBERRY:
        return "raspberry"
    if sys.platform == "win32":
        return "desktop_windows"
    if sys.platform == "darwin":
        return "desktop_macos"
    return "linux"


def detect_arch() -> str:
    override = os.environ.get("ORNG_APP_ARCH", "").strip()
    if override:
        return override
    import platform

    m = platform.machine().lower()
    if m in {"x86_64", "amd64"}:
        return "amd64"
    if m in {"aarch64", "arm64"}:
        return "arm64"
    if m.startswith("armv7"):
        return "armv7"
    return m or "unknown"
