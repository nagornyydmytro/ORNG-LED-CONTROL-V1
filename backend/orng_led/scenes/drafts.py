"""Durable scene drafts. Editing writes here and does not touch the live engine."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Any

from orng_led.config.io import parse_model, save_model
from orng_led.config.models import (
    AppConfig,
    ConfigError,
    FixtureProfile,
    PatchDocument,
    SpatialLayout,
)

_META = ".meta.json"
_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


def draft_lock(scene_id: str) -> threading.Lock:
    with _GUARD:
        lock = _LOCKS.get(scene_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[scene_id] = lock
        return lock


def draft_dir(config_root: Path, scene_id: str) -> Path:
    return Path(config_root) / "drafts" / scene_id


def read_meta(config_root: Path, scene_id: str) -> dict[str, Any]:
    path = draft_dir(config_root, scene_id) / _META
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_meta(config_root: Path, scene_id: str, meta: dict[str, Any]) -> None:
    root = draft_dir(config_root, scene_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / _META).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def remember_base(config_root: Path, scene_id: str, base_version: int | None) -> int | None:
    """Keep the revision the draft was opened from. Later saves do not move it."""
    with draft_lock(scene_id):
        return _remember_base(config_root, scene_id, base_version)


def _remember_base(config_root: Path, scene_id: str, base_version: int | None) -> int | None:
    meta = read_meta(config_root, scene_id)
    stored = meta.get("base_version")
    if stored is None and base_version is not None:
        meta["base_version"] = int(base_version)
        meta["scene_id"] = scene_id
        _write_meta(config_root, scene_id, meta)
        return int(base_version)
    if stored is None:
        return None
    return int(stored)


def tombstones(config_root: Path, scene_id: str) -> set[str]:
    raw = read_meta(config_root, scene_id).get("deleted")
    if not isinstance(raw, list):
        return set()
    return {str(item) for item in raw}


def stage_document(
    config_root: Path,
    scene_id: str,
    relative: str,
    model: AppConfig | FixtureProfile | PatchDocument | SpatialLayout,
    *,
    base_version: int | None,
) -> dict[str, Any]:
    if relative.startswith("/") or ".." in Path(relative).parts:
        raise ConfigError("draft path is not allowed")
    with draft_lock(scene_id):
        root = draft_dir(config_root, scene_id)
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        save_model(target, model)
        _forget_tombstone(config_root, scene_id, relative)
        stored = _remember_base(config_root, scene_id, base_version)
    return {"scene_id": scene_id, "path": relative, "base_version": stored}


def _forget_tombstone(config_root: Path, scene_id: str, relative: str) -> None:
    meta = read_meta(config_root, scene_id)
    deleted = tombstones(config_root, scene_id)
    if relative not in deleted:
        return
    deleted.remove(relative)
    meta["deleted"] = sorted(deleted)
    _write_meta(config_root, scene_id, meta)


def mark_deleted(
    config_root: Path,
    scene_id: str,
    relative: str,
    *,
    base_version: int | None,
) -> None:
    if relative.startswith("/") or ".." in Path(relative).parts:
        raise ConfigError("draft path is not allowed")
    with draft_lock(scene_id):
        path = draft_dir(config_root, scene_id) / relative
        if path.is_file():
            path.unlink()
        meta = read_meta(config_root, scene_id)
        deleted = tombstones(config_root, scene_id)
        deleted.add(relative)
        meta["deleted"] = sorted(deleted)
        meta["scene_id"] = scene_id
        _write_meta(config_root, scene_id, meta)
        _remember_base(config_root, scene_id, base_version)


def retarget_pad_after_delete(
    live_dir: Path,
    config_root: Path,
    scene_id: str,
    preset_id: str,
    *,
    base_version: int | None,
) -> None:
    """A deleted preset leaves the pad only when another preset can take the slot."""
    import yaml

    draft_app = draft_dir(config_root, scene_id) / "app.yaml"
    source = draft_app if draft_app.is_file() else Path(live_dir) / "app.yaml"
    if not source.is_file():
        return
    loaded = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return
    slots = loaded.get("pad_presets")
    if not isinstance(slots, list) or preset_id not in [str(item) for item in slots]:
        return
    remaining = [
        doc.id
        for doc in load_overlay_presets(live_dir, config_root, scene_id)
        if doc.id != preset_id
    ]
    used = [str(item) for item in slots if str(item) != preset_id]
    pool = [item for item in remaining if item not in used]
    next_slots: list[str] = []
    for item in slots:
        if str(item) != preset_id:
            next_slots.append(str(item))
            continue
        if not pool:
            return
        next_slots.append(pool.pop(0))
    if next_slots == [str(item) for item in slots]:
        return
    loaded["pad_presets"] = next_slots
    stage_document(
        config_root,
        scene_id,
        "app.yaml",
        parse_app(loaded),
        base_version=base_version,
    )


def overlay_scene_dir(live_dir: Path, config_root: Path, scene_id: str, dest: Path) -> bool:
    """Copy the published scene, then the draft. Returns whether a draft existed."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(live_dir, dest)
    root = draft_dir(config_root, scene_id)
    if not root.is_dir():
        return False
    found = False
    for path in root.rglob("*"):
        if not path.is_file() or path.name == _META:
            continue
        rel = path.relative_to(root)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        found = True
    for relative in tombstones(config_root, scene_id):
        target = dest / relative
        if target.is_file():
            target.unlink()
            found = True
    return found


def discard_if_unchanged(config_root: Path, scene_id: str, fingerprint: str) -> bool:
    """Delete the draft only if it is still the snapshot that was published.

    Returns True when a newer edit was kept. The check and the delete share
    the draft lock, so a save cannot land between them and disappear.
    """
    with draft_lock(scene_id):
        if draft_fingerprint(config_root, scene_id) != fingerprint:
            return True
        root = draft_dir(config_root, scene_id)
        if root.is_dir():
            shutil.rmtree(root)
        return False


def draft_fingerprint(config_root: Path, scene_id: str) -> str:
    root = draft_dir(config_root, scene_id)
    if not root.is_dir():
        return ""
    parts: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != _META:
            parts.append(f"{path.relative_to(root).as_posix()}:{path.read_bytes().hex()}")
    deleted = sorted(tombstones(config_root, scene_id))
    if deleted:
        parts.append("deleted:" + ",".join(deleted))
    return "|".join(parts)


def overlay_paths(live_dir: Path, config_root: Path, scene_id: str, folder: str) -> dict[str, Path]:
    """Draft file wins. A tombstone hides the live file. The live tree is not written."""
    found: dict[str, Path] = {}
    live = Path(live_dir) / folder
    if live.is_dir():
        for path in sorted(live.glob("*.yaml")):
            found[f"{folder}/{path.name}"] = path
    root = draft_dir(config_root, scene_id) / folder
    if root.is_dir():
        for path in sorted(root.glob("*.yaml")):
            found[f"{folder}/{path.name}"] = path
    hidden = tombstones(config_root, scene_id)
    for relative in list(found):
        if relative in hidden:
            del found[relative]
    return found


def write_draft_preset(
    config_root: Path,
    scene_id: str,
    document: Any,
    *,
    base_version: int | None,
) -> None:
    from orng_led.presets.io import save_preset

    relative = f"presets/{document.id}.yaml"
    with draft_lock(scene_id):
        target = draft_dir(config_root, scene_id) / relative
        save_preset(target, document)
        _forget_tombstone(config_root, scene_id, relative)
        _remember_base(config_root, scene_id, base_version)


def load_overlay_presets(live_dir: Path, config_root: Path, scene_id: str) -> list[Any]:
    from orng_led.presets.io import load_preset

    docs = []
    for path in overlay_paths(live_dir, config_root, scene_id, "presets").values():
        docs.append(load_preset(path))
    docs.sort(key=lambda doc: doc.id)
    return docs


def load_overlay_preset(live_dir: Path, config_root: Path, scene_id: str, preset_id: str) -> Any:
    relative = f"presets/{preset_id}.yaml"
    paths = overlay_paths(live_dir, config_root, scene_id, "presets")
    path = paths.get(relative)
    if path is None:
        raise KeyError(preset_id)
    from orng_led.presets.io import load_preset

    return load_preset(path)


def load_overlay_profiles(
    live_dir: Path, config_root: Path, scene_id: str
) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    for relative, path in overlay_paths(live_dir, config_root, scene_id, "profiles").items():
        profile_id = Path(relative).stem
        loaded = load_draft_document(config_root, scene_id, relative)
        if loaded is None:
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            loaded = data if isinstance(data, dict) else {}
        profiles[profile_id] = loaded
    return profiles


def load_draft_document(config_root: Path, scene_id: str, relative: str) -> dict[str, Any] | None:
    path = draft_dir(config_root, scene_id) / relative
    if not path.is_file():
        return None
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def parse_app(data: dict[str, Any]) -> AppConfig:
    app = parse_model(AppConfig, data, source="app.yaml")
    return app.model_copy(
        update={
            "output_armed": False,
            "artnet": app.artnet.model_copy(update={"hardware_verified": False}),
        }
    )


def parse_patch(data: dict[str, Any]) -> PatchDocument:
    return parse_model(PatchDocument, data, source="patch.yaml")


def parse_layout(data: dict[str, Any]) -> SpatialLayout:
    return parse_model(SpatialLayout, data, source="layout.yaml")


def parse_profile(data: dict[str, Any], profile_id: str) -> FixtureProfile:
    profile = parse_model(FixtureProfile, {**data, "id": profile_id}, source="profile")
    return profile.model_copy(update={"hardware_verified": False})
