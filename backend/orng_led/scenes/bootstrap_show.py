"""Minimal in-memory show for first boot with no assigned scene."""

from __future__ import annotations

from orng_led.config.models import (
    DEFAULT_PAD_PRESETS,
    AppConfig,
    PatchDocument,
    ShowConfig,
    SpatialLayout,
    TransportMode,
)
from orng_led.scenes.models import SceneControls, SceneManifest

EMPTY_SCENE_ID = "bootstrap-empty"
EMPTY_SCENE_NAME = "Safe bootstrap (no scene)"


def empty_safe_show() -> ShowConfig:
    """Mock transport, disarmed, zero fixtures — safe without Art-Net or scene YAML."""
    return ShowConfig(
        app=AppConfig(
            transport=TransportMode.MOCK,
            output_armed=False,
            pad_presets=list(DEFAULT_PAD_PRESETS),
        ),
        profiles={},
        patch=PatchDocument(fixtures=[]),
        layout=SpatialLayout(fixtures=[], placements=[], cable_chain=[]),
    )


def empty_safe_manifest() -> SceneManifest:
    return SceneManifest(scene_id=EMPTY_SCENE_ID, name=EMPTY_SCENE_NAME)


def empty_safe_controls() -> SceneControls:
    return SceneControls()
