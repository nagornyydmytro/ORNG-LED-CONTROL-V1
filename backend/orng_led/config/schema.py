"""Configuration schema constants for ORNG LED CONTROL."""

from __future__ import annotations

# Bump only with an intentional migration path. Unknown versions are rejected.
SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({SCHEMA_VERSION})

DMX_CHANNEL_MIN = 1
DMX_CHANNEL_MAX = 512
DMX_UNIVERSE_SIZE = 512
