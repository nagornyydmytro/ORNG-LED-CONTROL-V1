"""Scene bundle pack/validate and cloud agent command mapping."""

from __future__ import annotations

from pathlib import Path

import pytest

from orng_led.cloud.agent import map_command_to_action
from orng_led.cloud.scene_bundle import (
    is_allowed_scene_path,
    pack_scene_bundle,
    unpack_scene_bundle,
    validate_bundle_paths,
)
from orng_led.config.models import ConfigError


def test_path_allowlist_rejects_traversal() -> None:
    assert is_allowed_scene_path("app.yaml")
    assert is_allowed_scene_path("presets/P01.yaml")
    assert not is_allowed_scene_path("../secret.yaml")
    assert not is_allowed_scene_path("runtime.yaml")
    with pytest.raises(ConfigError):
        validate_bundle_paths(["../../etc/passwd"])


def test_pack_and_unpack_roundtrip(tmp_path: Path) -> None:
    # Use fixture scene if present, else minimal synthetic that won't full-validate.
    repo = Path(__file__).resolve().parents[2]
    scene = repo / "config" / "scenes" / "orng-bar"
    if not scene.is_dir():
        pytest.skip("orng-bar scene not present")

    raw, manifest = pack_scene_bundle(scene)
    assert manifest["content_sha256"]
    assert "app.yaml" in manifest["files"]
    files, content_hash = unpack_scene_bundle(raw)
    assert "app.yaml" in files
    assert content_hash == manifest["content_sha256"]

    # Corrupt bundle must fail without producing usable files
    with pytest.raises(ConfigError):
        unpack_scene_bundle(b"{not-json")


def test_agent_command_mapping() -> None:
    assert map_command_to_action("strobe_press") == "strobe_press"
    assert map_command_to_action("release_source") == "release_source"
    assert map_command_to_action("artnet_frame") is None
    assert map_command_to_action("select_pad") == "select_pad_slot"
