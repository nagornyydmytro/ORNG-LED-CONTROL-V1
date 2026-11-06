"""Strict portable identifiers shared with the Cloudflare Worker contract."""

from __future__ import annotations

import re
from pathlib import Path

from orng_led.config.models import ConfigError

# Must match cloud/src/scenes/releases.ts STRICT_SCENE_ID_RE
STRICT_SCENE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
STAGING_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def assert_safe_scene_id(scene_id: str) -> str:
    cleaned = str(scene_id or "").strip()
    if not cleaned or not STRICT_SCENE_ID_RE.fullmatch(cleaned):
        raise ConfigError(
            f"Invalid scene_id {scene_id!r}; must match {STRICT_SCENE_ID_RE.pattern}"
        )
    if ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        raise ConfigError(f"Invalid scene_id {scene_id!r}")
    return cleaned


def sanitize_staging_token(raw: str | None) -> str:
    """Portable staging suffix: only [A-Za-z0-9._-], no path separators or drive letters."""
    text = str(raw or "").strip()
    if not text:
        return "cmd"
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", text)[:64].strip("-._")
    if not cleaned or not STAGING_TOKEN_RE.fullmatch(cleaned):
        return "cmd"
    if ".." in cleaned:
        return "cmd"
    return cleaned


def resolve_under_scenes_root(scenes_root: Path, *parts: str) -> Path:
    """Resolve path under scenes_root; raise if escape attempted."""
    root = Path(scenes_root).resolve()
    candidate = root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ConfigError(
            f"Path escape rejected: {candidate} is outside {root}"
        ) from exc
    return candidate
