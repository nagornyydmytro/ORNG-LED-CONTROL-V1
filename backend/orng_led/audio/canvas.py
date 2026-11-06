"""Semantic stage canvas for Auto. Not a second renderer.

Bars and other fixtures share one coordinate field. Segment 0 is the
semantic bottom of a bar. ``invert_segments`` is not applied here; the
renderer flips wiring once.
"""

from __future__ import annotations

from dataclasses import dataclass

from orng_led.config.models import FixtureKind

_DEFAULT_SEGMENTS = 8
_DEFAULT_BAR_HEIGHT = 0.30


@dataclass(frozen=True)
class Cell:
    fixture_id: str
    kind: FixtureKind
    segment: int
    x: float
    y: float
    z: float
    up: float
    bar_index: int


@dataclass(frozen=True)
class Canvas:
    cells: tuple[Cell, ...]
    by_fixture: dict[str, tuple[Cell, ...]]
    kinds: frozenset[FixtureKind]
    bar_ids: tuple[str, ...]


def _point(fixture: object, placements: object) -> tuple[float, float, float, float] | None:
    """x, y, z, height. None when the fixture has no plan position."""
    fixture_id = str(getattr(fixture, "id", ""))
    if placements is not None:
        for placement in placements:
            if str(getattr(placement, "fixture_id", "")) != fixture_id:
                continue
            height = float(getattr(placement, "height", 0.0) or 0.0)
            return (
                float(placement.x),
                float(placement.y),
                float(getattr(placement, "z", 0.0)),
                height,
            )
    raw_x = getattr(fixture, "layout_x", None)
    raw_y = getattr(fixture, "layout_y", None)
    if raw_x is None or raw_y is None:
        nested = getattr(fixture, "placement", None)
        if nested is None or not hasattr(nested, "x") or not hasattr(nested, "y"):
            return None
        raw_x = nested.x
        raw_y = nested.y
        raw_z = getattr(nested, "z", 0.0)
        height = float(getattr(nested, "height", 0.0) or 0.0)
    else:
        raw_z = getattr(fixture, "layout_z", 0.0)
        height = 0.0
    try:
        return (float(raw_x), float(raw_y), float(raw_z), height)
    except (TypeError, ValueError):
        return None


def segment_count(fixture: object) -> int:
    spatial = getattr(fixture, "spatial", None)
    raw = getattr(spatial, "segment_count", None)
    if raw is None:
        raw = getattr(fixture, "segment_count", None)
    try:
        count = int(raw) if raw is not None else _DEFAULT_SEGMENTS
    except (TypeError, ValueError):
        count = _DEFAULT_SEGMENTS
    return max(1, min(8, count))


def build_canvas(fixtures: list[object], placements: object = None) -> Canvas:
    """One cell per bar segment, one cell per PAR or beam. Face is omitted."""
    bars: list[tuple[str, object, tuple[float, float, float, float]]] = []
    others: list[tuple[str, object, tuple[float, float, float, float]]] = []
    for fixture in fixtures:
        kind = getattr(fixture, "kind", None)
        if kind is FixtureKind.FACE_PAR:
            continue
        if kind not in {FixtureKind.BAR, FixtureKind.PAR, FixtureKind.BEAM}:
            continue
        point = _point(fixture, placements)
        if point is None:
            continue
        fixture_id = str(fixture.id)
        if kind is FixtureKind.BAR:
            bars.append((fixture_id, fixture, point))
        else:
            others.append((fixture_id, fixture, point))
    bars.sort(key=lambda item: (item[2][0], item[0]))
    bar_ids = tuple(item[0] for item in bars)
    index_of = {fixture_id: index for index, fixture_id in enumerate(bar_ids)}
    cells: list[Cell] = []
    grouped: dict[str, list[Cell]] = {}
    kinds: set[FixtureKind] = set()
    for fixture_id, fixture, (x, y, z, height) in bars:
        count = segment_count(fixture)
        span = height if height > 0.02 else _DEFAULT_BAR_HEIGHT
        kinds.add(FixtureKind.BAR)
        bar_index = index_of[fixture_id]
        for segment in range(count):
            # Segment 0 is the semantic bottom. Layout y grows downward.
            up = (segment + 0.5) / float(count)
            cell_y = y + (0.5 - up) * span
            cell = Cell(
                fixture_id=fixture_id,
                kind=FixtureKind.BAR,
                segment=segment,
                x=x,
                y=cell_y,
                z=z,
                up=up,
                bar_index=bar_index,
            )
            cells.append(cell)
            grouped.setdefault(fixture_id, []).append(cell)
    for fixture_id, fixture, (x, y, z, _height) in others:
        kind = fixture.kind
        kinds.add(kind)
        cell = Cell(
            fixture_id=fixture_id,
            kind=kind,
            segment=0,
            x=x,
            y=y,
            z=z,
            up=max(0.0, min(1.0, 1.0 - float(y))),
            bar_index=-1,
        )
        cells.append(cell)
        grouped.setdefault(fixture_id, []).append(cell)
    return Canvas(
        cells=tuple(cells),
        by_fixture={key: tuple(value) for key, value in grouped.items()},
        kinds=frozenset(kinds),
        bar_ids=bar_ids,
    )
