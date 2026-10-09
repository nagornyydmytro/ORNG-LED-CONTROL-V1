"""Near-static atmosphere presets A01–A03 (always-lit bars, slow wash)."""

from __future__ import annotations

from orng_led.presets.models import EpisodeCard, PresetDocument

ATMOSPHERE_PRESET_IDS = ("A01", "A02", "A03")

ATMOSPHERE_LABELS: dict[str, str] = {
    "A01": "Статика — червоний",
    "A02": "Статика — синій",
    "A03": "Статика — фіолетовий",
}

# Exactly one vertical bar-motion episode per preset (the rest stay solid glow).
_VERTICAL_EFFECTS = frozenset({"glow_line", "glow_pinch", "glow_eq"})
_ALLOWED_EFFECTS = frozenset({"glow"}) | _VERTICAL_EFFECTS


def _ep(
    index: int,
    *,
    groups: list[str],
    palette: str,
    effect: str,
    speed: float,
    intensity: float,
) -> EpisodeCard:
    return EpisodeCard(
        id=f"ep{index}",
        duration_s=18.0,
        groups=groups,
        palette=palette,
        effect=effect,
        speed=speed,
        intensity=intensity,
        transition="cut",
    )


def build_atmosphere_preset(preset_id: str) -> PresetDocument:
    label = ATMOSPHERE_LABELS[preset_id]
    episodes = _EPISODES[preset_id]
    if len(episodes) != 10:
        raise ValueError(f"{preset_id} must have exactly 10 episodes")
    vertical = [ep for ep in episodes if ep.effect in _VERTICAL_EFFECTS]
    if len(vertical) != 1:
        raise ValueError(f"{preset_id} must have exactly one vertical bar episode")
    for ep in episodes:
        if "bar" not in ep.groups and "all_rear" not in ep.groups:
            raise ValueError(f"{preset_id}/{ep.id} must keep bars lit")
        if ep.effect not in _ALLOWED_EFFECTS:
            raise ValueError(f"{preset_id}/{ep.id} unexpected effect {ep.effect!r}")
        if ep.speed > 0.25:
            raise ValueError(f"{preset_id}/{ep.id} speed too high for near-static")
    return PresetDocument(
        id=preset_id,
        label=label,
        hardware_tuned=False,
        builtin=True,
        episodes=episodes,
    )


def all_atmosphere_presets() -> dict[str, PresetDocument]:
    return {preset_id: build_atmosphere_preset(preset_id) for preset_id in ATMOSPHERE_PRESET_IDS}


# Near-static washes: solid ``glow`` everywhere except one vertical bar episode (ep8).
# A01 stays red-family only (no amber). Beams stay on from ep4 so ep5 all_rear has no head pop.
_EPISODES: dict[str, list[EpisodeCard]] = {
    "A01": [
        _ep(1, groups=["bar", "par"], palette="deep_red", effect="glow", speed=0.1, intensity=0.9),
        _ep(2, groups=["bar"], palette="rose", effect="glow", speed=0.1, intensity=0.88),
        _ep(3, groups=["bar", "beam"], palette="deep_red", effect="glow", speed=0.09, intensity=0.92),
        _ep(4, groups=["bar", "par", "beam"], palette="rose", effect="glow", speed=0.1, intensity=0.88),
        _ep(5, groups=["all_rear"], palette="deep_red", effect="glow", speed=0.1, intensity=0.9),
        _ep(6, groups=["bar", "beam"], palette="rose", effect="glow", speed=0.1, intensity=0.9),
        _ep(7, groups=["bar", "par"], palette="deep_red", effect="glow", speed=0.08, intensity=0.93),
        # Sync lit band bouncing up/down on every bar at once.
        _ep(8, groups=["bar"], palette="deep_red", effect="glow_line", speed=0.16, intensity=0.92),
        _ep(9, groups=["bar", "beam", "par"], palette="rose", effect="glow", speed=0.1, intensity=0.9),
        _ep(10, groups=["all_rear"], palette="deep_red", effect="glow", speed=0.1, intensity=0.9),
    ],
    "A02": [
        _ep(1, groups=["bar", "par"], palette="cool_blue", effect="glow", speed=0.1, intensity=0.9),
        _ep(2, groups=["bar"], palette="deep_blue", effect="glow", speed=0.1, intensity=0.88),
        _ep(3, groups=["bar", "beam"], palette="cool_blue", effect="glow", speed=0.09, intensity=0.92),
        _ep(4, groups=["bar", "par", "beam"], palette="deep_blue", effect="glow", speed=0.1, intensity=0.88),
        _ep(5, groups=["all_rear"], palette="cool_blue", effect="glow", speed=0.1, intensity=0.9),
        _ep(6, groups=["bar", "beam"], palette="deep_blue", effect="glow", speed=0.1, intensity=0.9),
        _ep(7, groups=["bar", "par"], palette="cool_blue", effect="glow", speed=0.08, intensity=0.93),
        # Top + bottom grow toward center, then retract — same on every bar.
        _ep(8, groups=["bar"], palette="deep_blue", effect="glow_pinch", speed=0.16, intensity=0.92),
        _ep(9, groups=["bar", "beam", "par"], palette="cool_blue", effect="glow", speed=0.1, intensity=0.9),
        _ep(10, groups=["all_rear"], palette="deep_blue", effect="glow", speed=0.1, intensity=0.9),
    ],
    "A03": [
        _ep(1, groups=["bar", "par"], palette="violet", effect="glow", speed=0.1, intensity=0.9),
        _ep(2, groups=["bar"], palette="magenta", effect="glow", speed=0.1, intensity=0.88),
        _ep(3, groups=["bar", "beam"], palette="violet", effect="glow", speed=0.09, intensity=0.92),
        _ep(4, groups=["bar", "par", "beam"], palette="violet", effect="glow", speed=0.1, intensity=0.88),
        _ep(5, groups=["all_rear"], palette="magenta", effect="glow", speed=0.1, intensity=0.9),
        _ep(6, groups=["bar", "beam"], palette="violet", effect="glow", speed=0.1, intensity=0.9),
        _ep(7, groups=["bar", "par"], palette="violet", effect="glow", speed=0.08, intensity=0.93),
        # Standing-wave / equalizer columns across the bar row.
        _ep(8, groups=["bar"], palette="magenta", effect="glow_eq", speed=0.16, intensity=0.92),
        _ep(9, groups=["bar", "beam", "par"], palette="violet", effect="glow", speed=0.1, intensity=0.9),
        _ep(10, groups=["all_rear"], palette="magenta", effect="glow", speed=0.1, intensity=0.9),
    ],
}
