"""Managed installation layout — app root vs data root.

Updater never writes into a developer git checkout. Data (scenes, credentials,
cloud metadata) lives outside release directories.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ManagedPaths:
    app_root: Path
    data_root: Path

    @property
    def bootstrap(self) -> Path:
        return self.app_root / "bootstrap"

    @property
    def releases(self) -> Path:
        return self.app_root / "releases"

    @property
    def current_pointer(self) -> Path:
        return self.app_root / "current.json"

    @property
    def previous_pointer(self) -> Path:
        return self.app_root / "previous.json"

    @property
    def update_state(self) -> Path:
        return self.app_root / "update-state.json"

    @property
    def logs(self) -> Path:
        return self.app_root / "logs"

    @property
    def scenes(self) -> Path:
        return self.data_root / "scenes"

    @property
    def cloud_metadata(self) -> Path:
        return self.data_root / "cloud-metadata.json"


def default_app_root() -> Path:
    override = os.environ.get("ORNG_APP_ROOT", "").strip()
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "ORNG-LED-CONTROL"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ORNG-LED-CONTROL" / "app"
    return Path("/opt/orng-led-control")


def default_data_root() -> Path:
    override = os.environ.get("ORNG_CONFIG_ROOT") or os.environ.get("ORNG_DATA_ROOT")
    if override:
        return Path(override.strip())
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "ORNG-LED-CONTROL" / "data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ORNG-LED-CONTROL" / "data"
    return Path("/var/lib/orng-led-control")


def resolve_managed_paths() -> ManagedPaths:
    return ManagedPaths(app_root=default_app_root(), data_root=default_data_root())


def ensure_managed_layout(paths: ManagedPaths | None = None) -> ManagedPaths:
    paths = paths or resolve_managed_paths()
    for d in (
        paths.app_root,
        paths.bootstrap,
        paths.releases,
        paths.logs,
        paths.data_root,
        paths.scenes,
    ):
        d.mkdir(parents=True, exist_ok=True)
    return paths


def read_pointer(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_pointer_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def release_dir(paths: ManagedPaths, version: str, build_id: str) -> Path:
    safe_v = version.replace("/", "-").replace("\\", "-")
    safe_b = build_id.replace("/", "-").replace("\\", "-")
    return paths.releases / f"{safe_v}-{safe_b}"


def staging_dir(paths: ManagedPaths, staging_id: str) -> Path:
    safe = staging_id.replace("/", "-").replace("\\", "-")
    return paths.releases / ".staging" / safe


def apply_request_path(paths: ManagedPaths) -> Path:
    return paths.app_root / "apply-request.json"


def assert_release_path_contained(paths: ManagedPaths, candidate: Path) -> Path:
    """Ensure candidate resolves inside releases/ and is not a symlink."""
    releases = paths.releases.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(releases)
    except ValueError as exc:
        raise ValueError(f"Path escapes releases/: {candidate}") from exc
    if candidate.exists() and candidate.is_symlink():
        raise ValueError(f"Symlink releases are forbidden: {candidate}")
    if resolved.exists() and resolved.is_symlink():
        raise ValueError(f"Symlink releases are forbidden: {candidate}")
    return resolved
