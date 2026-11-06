"""Auto palettes as compositional colour roles.

Slots are discrete wheel names the bar profile actually has. RGB heads can
mix; bars snap to the nearest named slot. A palette does not shuffle colour
every frame.
"""

from __future__ import annotations

from dataclasses import dataclass

from orng_led.config.models import FixtureKind
from orng_led.engine.intents import Rgbw

# Same named swatches as the renderer wheel. Do not invent a slot bars lack.
WHEEL_RGB: dict[str, tuple[float, float, float]] = {
    "off": (0.0, 0.0, 0.0),
    "red": (1.0, 0.0, 0.0),
    "orange": (1.0, 0.35, 0.05),
    "green": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "white": (1.0, 1.0, 1.0),
    "amber": (1.0, 0.55, 0.0),
    "cyan": (0.0, 1.0, 1.0),
    "purple": (0.7, 0.0, 1.0),
}

WHEEL_DISPLAY: dict[str, str] = {
    "red": "#ff1405",
    "orange": "#ff6b00",
    "amber": "#ffe61f",
    "green": "#1eff2e",
    "blue": "#1e47ff",
    "cyan": "#00f2f2",
    "purple": "#c71eff",
    "white": "#fff5e6",
}

# Operator ids that are not wheel names.
_ALIAS = {"violet": "purple"}

LEGACY_CAST = {"bars": "bars", "heads": "heads"}


@dataclass(frozen=True)
class AutoPalette:
    id: str
    title: str
    kind: str
    slots: tuple[str, ...]
    layout: str
    note: str = ""


PALETTES: tuple[AutoPalette, ...] = (
    AutoPalette("orange", "Помаранчевий", "mono", ("orange",), "uniform"),
    AutoPalette("red", "Червоний", "mono", ("red",), "uniform"),
    AutoPalette("blue", "Синій", "mono", ("blue",), "uniform"),
    AutoPalette("violet", "Фіолетовий", "mono", ("purple",), "uniform", "Колесо бара — purple."),
    AutoPalette("amber", "Бурштиновий", "warm", ("amber",), "uniform"),
    AutoPalette("white", "Білий", "mono", ("white",), "uniform"),
    AutoPalette("ember", "Жар", "dual", ("orange", "red"), "kind", "Бари помаранчеві, PAR червоні."),
    AutoPalette("sunset", "Захід", "warm", ("orange", "amber"), "x", "Ліво помаранч, право бурштин."),
    AutoPalette("ice", "Лід", "cool", ("blue", "cyan"), "kind", "Бари сині, голови ціан."),
    AutoPalette("night", "Ніч", "cool", ("blue", "white"), "kind"),
    AutoPalette("contrast", "Контраст", "contrast", ("orange", "blue"), "x", "Тепло / холод по сцені."),
    AutoPalette("forest", "Ліс", "dual", ("green", "amber"), "x"),
    AutoPalette("carnival", "Карнавал", "multi", ("orange", "purple", "cyan"), "bar"),
    AutoPalette("traffic", "Світлофор", "multi", ("red", "amber", "green"), "bar"),
)

PALETTE_BY_ID: dict[str, AutoPalette] = {item.id: item for item in PALETTES}
COLOR_IDS: tuple[str, ...] = tuple(item.id for item in PALETTES)
assert len(COLOR_IDS) >= 12


def wheel_name(slot: str) -> str:
    return _ALIAS.get(slot, slot)


def rgbw_of(slot: str) -> Rgbw:
    name = wheel_name(slot)
    red, green, blue = WHEEL_RGB.get(name, WHEEL_RGB["orange"])
    white = 0.55 if name == "white" else 0.0
    return Rgbw(r=red, g=green, b=blue, w=white)


def migrate_palette(value: str | None) -> tuple[str, str | None]:
    """Return (palette id, cast override). Bars/heads are not colours."""
    name = str(value or "orange")
    if name in LEGACY_CAST:
        return "orange" if name == "bars" else "white", name
    if name in PALETTE_BY_ID:
        return name, None
    if name == "purple":
        return "violet", None
    return "orange", None


def known_palette(value: str | None) -> bool:
    name = str(value or "")
    return name in PALETTE_BY_ID or name in LEGACY_CAST


def palette_of(value: str | None, fallback: str = "orange") -> AutoPalette:
    ident, _cast = migrate_palette(value)
    return PALETTE_BY_ID.get(ident, PALETTE_BY_ID[fallback])


def color_for_fixture(
    palette: AutoPalette | str | None,
    *,
    kind: FixtureKind | str,
    x: float = 0.5,
    bar_index: int = 0,
) -> Rgbw:
    """Stable colour for one fixture. Phase does not pick a new slot."""
    chosen = palette if isinstance(palette, AutoPalette) else palette_of(palette)
    slots = chosen.slots
    if not slots:
        return rgbw_of("orange")
    kind_name = kind.value if isinstance(kind, FixtureKind) else str(kind)
    if chosen.layout == "kind":
        index = 0 if kind_name == "bar" else min(1, len(slots) - 1)
    elif chosen.layout == "x":
        index = 0 if float(x) < 0.5 else min(1, len(slots) - 1)
    elif chosen.layout == "bar":
        index = int(bar_index) % len(slots) if kind_name == "bar" else 0
    elif chosen.layout == "layer":
        if kind_name == "bar":
            index = 0
        elif kind_name == "par":
            index = min(1, len(slots) - 1)
        else:
            index = min(2, len(slots) - 1)
    else:
        index = 0
    return rgbw_of(slots[index])


def catalog() -> tuple[dict[str, object], ...]:
    rows = []
    for item in PALETTES:
        rows.append(
            {
                "id": item.id,
                "title": item.title,
                "kind": item.kind,
                "slots": list(item.slots),
                "layout": item.layout,
                "note": item.note,
                "swatches": [WHEEL_DISPLAY[wheel_name(slot)] for slot in item.slots],
            }
        )
    return tuple(rows)
