"""Validate presets against the show-mode whitelist contract.

Semantic YAML presets must not carry raw DMX or fixture-internal roles.
The renderer still scrubs the final frame; this rejects bad documents early.
"""

from __future__ import annotations

from orng_led.config.models import ChannelRole, ConfigError
from orng_led.presets.models import EpisodeCard, PresetDocument

# Roles that must never appear as preset-driven show output.
FORBIDDEN_PRESET_ROLE_NAMES = frozenset(
    {
        ChannelRole.PROGRAM.value,
        ChannelRole.EFFECT_SPEED.value,
        ChannelRole.DIRECTION_MODE.value,
        ChannelRole.GOBO.value,
        ChannelRole.GOBO_ROTATION.value,
        ChannelRole.PRISM.value,
        ChannelRole.PRISM_ROTATION.value,
        ChannelRole.MACRO.value,
        ChannelRole.SPEED.value,
        ChannelRole.UNKNOWN.value,
        ChannelRole.SHUTTER.value,
        ChannelRole.STROBE.value,
        ChannelRole.COLOR.value,
        ChannelRole.MOVEMENT_SPEED.value,
        ChannelRole.RESET.value,
        ChannelRole.FOCUS.value,
        ChannelRole.ZOOM.value,
        ChannelRole.UV.value,
    }
)

FORBIDDEN_DOCUMENT_KEYS = frozenset(
    {
        "dmx",
        "channels",
        "raw",
        "raw_dmx",
        "universe",
        "program",
        "gobo",
        "prism",
        "macro",
        "effect_speed",
    }
)


def validate_episode_show_contract(episode: EpisodeCard) -> None:
    """Episodes are semantic only — reject anything that smells like raw DMX."""
    dump = episode.model_dump()
    for key in dump:
        if key in FORBIDDEN_DOCUMENT_KEYS:
            raise ConfigError(f"Episode {episode.id!r} contains forbidden key {key!r}")
    # Intensity/speed already bounded by the model; palette/effect enums too.


def validate_preset_show_contract(document: PresetDocument) -> None:
    """Raise ConfigError when a preset document violates the show whitelist contract."""
    raw = document.model_dump()
    for key in raw:
        if key in FORBIDDEN_DOCUMENT_KEYS:
            raise ConfigError(f"Preset {document.id!r} contains forbidden key {key!r}")
    for episode in document.episodes:
        validate_episode_show_contract(episode)


def assert_no_forbidden_role_name(role_name: str) -> None:
    if role_name in FORBIDDEN_PRESET_ROLE_NAMES:
        raise ConfigError(f"Forbidden show role in preset path: {role_name}")
