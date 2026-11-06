"""Device-local cloud metadata — outside scene bundles, git, and config/.

Tracks last-synced / applied / desired hashes so dirty vs update vs conflict
are distinguishable. Never stores tokens.

Paths:
  Linux/Raspberry: /var/lib/orng-led-control/cloud-metadata.json
  Windows: LocalAppData/ORNG-LED-CONTROL/
  macOS: Application Support/ORNG-LED-CONTROL/
  tests/dev: ORNG_CLOUD_METADATA_FILE override

``config/cloud-metadata.json`` is ignored (legacy protection).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SceneCloudMeta:
    last_synced_version: int | None = None
    last_synced_hash: str | None = None
    applied_version: int | None = None
    applied_hash: str | None = None
    desired_version: int | None = None
    desired_hash: str | None = None
    sync_status: str = "unknown"  # synced | dirty | update_available | conflict | error
    last_sync_error: str | None = None


@dataclass
class CloudMetadata:
    installation_id: str | None = None
    app_version: str | None = None
    build_id: str | None = None
    scenes: dict[str, SceneCloudMeta] = field(default_factory=dict)

    def scene(self, scene_id: str) -> SceneCloudMeta:
        if scene_id not in self.scenes:
            self.scenes[scene_id] = SceneCloudMeta()
        return self.scenes[scene_id]


def cloud_metadata_path(*_ignored: Any, **_kw: Any) -> Path:
    """Resolve device-local metadata path. ``config_root`` arguments are ignored."""
    override = os.environ.get("ORNG_CLOUD_METADATA_FILE")
    if override:
        return Path(override)

    if sys.platform.startswith("linux"):
        return Path("/var/lib/orng-led-control/cloud-metadata.json")
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "ORNG-LED-CONTROL" / "cloud-metadata.json"
    support = Path.home() / "Library" / "Application Support"
    return support / "ORNG-LED-CONTROL" / "cloud-metadata.json"


def load_cloud_metadata(*_ignored: Any, **_kw: Any) -> CloudMetadata:
    path = cloud_metadata_path()
    # Never read legacy config/cloud-metadata.json
    if "config" in path.parts and path.name == "cloud-metadata.json":
        if not os.environ.get("ORNG_CLOUD_METADATA_FILE"):
            return CloudMetadata()
    if not path.is_file():
        return CloudMetadata()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return CloudMetadata()
    scenes: dict[str, SceneCloudMeta] = {}
    for sid, entry in (raw.get("scenes") or {}).items():
        if not isinstance(entry, dict):
            continue
        scenes[str(sid)] = SceneCloudMeta(
            last_synced_version=_int_or_none(entry.get("last_synced_version")),
            last_synced_hash=_str_or_none(entry.get("last_synced_hash")),
            applied_version=_int_or_none(entry.get("applied_version")),
            applied_hash=_str_or_none(entry.get("applied_hash")),
            desired_version=_int_or_none(entry.get("desired_version")),
            desired_hash=_str_or_none(entry.get("desired_hash")),
            sync_status=str(entry.get("sync_status") or "unknown"),
            last_sync_error=_str_or_none(entry.get("last_sync_error")),
        )
    return CloudMetadata(
        installation_id=_str_or_none(raw.get("installation_id")),
        app_version=_str_or_none(raw.get("app_version")),
        build_id=_str_or_none(raw.get("build_id")),
        scenes=scenes,
    )


def save_cloud_metadata(meta: CloudMetadata, *_ignored: Any, **_kw: Any) -> Path:
    path = cloud_metadata_path()
    # Refuse writing under a repo config/ tree unless explicit override.
    if not os.environ.get("ORNG_CLOUD_METADATA_FILE"):
        parts_lower = {p.lower() for p in path.parts}
        if "config" in parts_lower and "scenes" not in str(path):
            # Extra guard — default paths never include config/
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "installation_id": meta.installation_id,
        "app_version": meta.app_version,
        "build_id": meta.build_id,
        "scenes": {sid: asdict(s) for sid, s in meta.scenes.items()},
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def classify_sync_state(
    *,
    local_hash: str | None,
    last_synced_hash: str | None,
    remote_hash: str | None,
) -> str:
    """Classify dirty / update_available / conflict / synced.

    - dirty: local differs from last synced (unpublished local edits)
    - update_available: remote moved ahead of last synced, local clean
    - conflict: local dirty AND remote ahead of last synced
    - synced: local matches last synced and remote matches
    """
    if last_synced_hash:
        dirty = bool(local_hash and local_hash != last_synced_hash)
        remote_ahead = bool(remote_hash and remote_hash != last_synced_hash)
    else:
        # No base → cannot claim local edits vs cloud base.
        dirty = False
        remote_ahead = bool(remote_hash and local_hash and remote_hash != local_hash)

    if dirty and remote_ahead:
        return "conflict"
    if dirty:
        return "dirty"
    if remote_ahead:
        return "update_available"
    if local_hash and remote_hash and local_hash == remote_hash:
        return "synced"
    if last_synced_hash and remote_hash and last_synced_hash == remote_hash and not dirty:
        return "synced"
    return "unknown"


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
