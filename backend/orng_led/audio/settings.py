"""Auto operator settings. Palette is colour. Cast is who is on stage."""

from __future__ import annotations

from orng_led.audio.palettes import COLOR_IDS, known_palette, migrate_palette
from orng_led.engine.clock import FRAME_DT, FPS

CAST_IDS = ("all", "bars", "heads")
CHARACTER_IDS = ("smooth", "rhythmic", "impact")
PERIOD_IDS = (0.5, 1.0, 2.0, 4.0, 8.0)
INTENSITY_IDS = ("soft", "normal", "full")
PERIODS_FOR_CHARACTER: dict[str, tuple[float | None, ...]] = {
    "smooth": (None, 2.0, 4.0, 8.0),
    "rhythmic": (None, 0.5, 1.0, 2.0, 4.0, 8.0),
    "impact": (None, 0.5, 1.0, 2.0, 4.0),
}

CAST_GROUPS: dict[str, tuple[str, ...]] = {
    "all": ("par", "bar", "beam"),
    "bars": ("bar",),
    "heads": ("par", "beam"),
}

INTENSITY_LEVELS: dict[str, float] = {
    "soft": 0.45,
    "normal": 0.72,
    "full": 1.0,
}

CHARACTER_FAMILIES: dict[str, frozenset[str]] = {
    "smooth": frozenset(
        {
            "breathe",
            "spatial_sine",
            "vertical_wave",
            "fill_drain",
            "open_close",
            "compound",
            "layer",
        }
    ),
    "rhythmic": frozenset(
        {
            "phase_pulse",
            "cascade",
            "call_response",
            "band_tail",
            "meet",
            "multi_band",
            "diagonal",
            "layer",
            "vertical_wave",
        }
    ),
    "impact": frozenset(
        {
            "hit_decay",
            "double_hit",
            "sparse_hits",
            "density_build",
            "dark_window",
            "negative",
            "cascade",
            "band_tail",
        }
    ),
}

# Lengths in beats. A loud reading does not collapse a phrase into an accent.
# phrase is the main motif hold, not the picture cycle.
CHARACTER_DURATION: dict[str, dict[str, int]] = {
    "smooth": {"phrase": 64, "calm": 32, "rise": 16, "accent": 4, "signal": 4},
    "rhythmic": {"phrase": 32, "calm": 16, "rise": 8, "accent": 2, "signal": 4},
    "impact": {"phrase": 32, "calm": 16, "rise": 8, "accent": 2, "signal": 4},
}

# How often the current motif may change cycle, density or a second layer.
CHARACTER_VARIATION: dict[str, int] = {
    "smooth": 16,
    "rhythmic": 8,
    "impact": 4,
}

# First motif the director tries when the scene can paint it.
DIRECTOR_HOME: dict[str, str] = {
    "smooth": "sine_vertical",
    "rhythmic": "vertical_wave",
    "impact": "cascade_up",
}
_HEADS_HOME: dict[str, str] = {
    "smooth": "breathe_heads",
    "rhythmic": "pulse",
    "impact": "hit_heads",
}


def director_home(character: str, theme: str | None = None, cast: str | None = None) -> str:
    """Home look for this character that the cast can actually paint."""
    chosen = cast_of(theme, cast)
    if chosen == "heads":
        return _HEADS_HOME.get(character, "pulse")
    return DIRECTOR_HOME.get(character, "vertical_wave")

CHARACTER_CYCLE: dict[str, dict[str, float]] = {
    "smooth": {"phrase": 8.0, "calm": 8.0, "rise": 4.0, "accent": 2.0},
    "rhythmic": {"phrase": 2.0, "calm": 4.0, "rise": 2.0, "accent": 1.0},
    "impact": {"phrase": 1.0, "calm": 2.0, "rise": 1.0, "accent": 1.0},
}

# One on-frame plus one off-frame is 15 Hz — the fastest 30 Hz can show.
MAX_FLASH_HZ = FPS / 2.0
MIN_FLASH_FRAMES = 1
MIN_GAP_FRAMES = 1


def split_theme(theme: str | None) -> tuple[str, str]:
    """Return (palette colour, cast). Bars/heads are not colours."""
    name = str(theme or "orange")
    if name in CAST_IDS and name != "all":
        colour, _cast = migrate_palette(name)
        return colour, name
    if known_palette(name):
        colour, extra = migrate_palette(name)
        return colour, extra or "all"
    if name == "all":
        return "orange", "all"
    return "orange", "all"


def cast_of(theme: str | None, cast: str | None = None) -> str:
    if cast in CAST_IDS:
        return str(cast)
    _colour, found = split_theme(theme)
    return found


def colour_of(theme: str | None, palette: str | None = None) -> str:
    if known_palette(palette):
        colour, _cast = migrate_palette(palette)
        return colour
    colour, _cast = split_theme(theme)
    return colour


def groups_of(cast: str) -> tuple[str, ...]:
    return CAST_GROUPS.get(cast, CAST_GROUPS["all"])


def intensity_of(name: str | float | None) -> float:
    if isinstance(name, (int, float)):
        return max(0.0, min(1.0, float(name)))
    return INTENSITY_LEVELS.get(str(name or "full"), 1.0)


def known_period(
    value: float | int | str | None, character: str | None = None
) -> float | None:
    if value is None or value == "auto" or value == "":
        return None
    number = float(value)
    allowed = PERIODS_FOR_CHARACTER.get(str(character or ""), PERIOD_IDS)
    for choice in allowed:
        if choice is None:
            continue
        if abs(number - choice) <= 1e-9:
            return choice
    return None


def frames_for_seconds(seconds: float) -> int:
    return max(MIN_FLASH_FRAMES, int(round(float(seconds) * FPS)))


def achievable_period_frames(density: float) -> int:
    """Rise densifies toward the 30 Hz limit. Never faster than on/off frames."""
    amount = max(0.0, min(1.0, float(density)))
    # 8 frames (~3.8 Hz) down to 2 frames (15 Hz).
    return max(2, int(round(8.0 - amount * 6.0)))


def series_period_frames(density: float, beat_period_s: float, character: str = "rhythmic") -> int:
    """On/off period from a beat subdivision, then clamped to a displayable frame pair."""
    beat = max(0.25, min(1.0, float(beat_period_s)))
    amount = max(0.0, min(1.0, float(density)))
    if character == "impact":
        amount = min(1.0, amount + 0.25)
    steps = (1.0, 0.5, 0.25, 0.125)
    index = min(len(steps) - 1, int(amount * len(steps)))
    span_s = beat * steps[index]
    return max(2, int(round(span_s / FRAME_DT)))
