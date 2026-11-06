"""Saved desk execution choice. A network error must not rewrite it."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from orng_led.desktop.output_role import desktop_output_role

ROLES = {"local", "raspberry"}
# The frame path reads this. A slow disk must not be on every UDP tick.
_ROLE_CACHE: dict[str, str] = {}


def _role_path(config_root: Path) -> Path:
    return Path(config_root) / "desk-role.json"


def _instance_path(config_root: Path) -> Path:
    return Path(config_root) / "instance.json"


def saved_desk_role(config_root: Path) -> str | None:
    path = _role_path(config_root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    role = str(data.get("role") or "")
    return role if role in ROLES else None


def desk_role(config_root: Path) -> str:
    """Saved choice wins. The environment is only the first-run default.

    After the first read, the frame path uses memory. ``set_desk_role`` updates it.
    """
    key = str(Path(config_root))
    cached = _ROLE_CACHE.get(key)
    if cached in ROLES:
        return cached
    saved = saved_desk_role(config_root)
    if saved:
        role = saved
    else:
        env_role = desktop_output_role()
        role = env_role if env_role in ROLES else "local"
    _ROLE_CACHE[key] = role
    return role


def set_desk_role(config_root: Path, role: str) -> str:
    if role not in ROLES:
        raise ValueError("role must be local or raspberry")
    path = _role_path(config_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"role": role}, ensure_ascii=False),
        encoding="utf-8",
    )
    _ROLE_CACHE[str(Path(config_root))] = role
    return role


def instance_id(config_root: Path) -> str:
    """Stable id of this installed desk. Not a proof by itself; the launcher also checks the process."""
    path = _instance_path(config_root)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            found = str(data.get("instance_id") or "")
            if found:
                return found
        except (OSError, json.JSONDecodeError):
            pass
    value = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"instance_id": value}), encoding="utf-8")
    return value


def product_data_root() -> Path | None:
    raw = os.environ.get("ORNG_CONFIG_ROOT", "").strip()
    return Path(raw) if raw else None
