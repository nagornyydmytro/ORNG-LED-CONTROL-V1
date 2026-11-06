"""Mirror pairs from the stage plan.

Left and right are the audience view: ``x`` grows to the audience right, so the
axis is ``x = 0.5`` and the reflection of a point is ``(1 - x, y, z)``. A pair
is two placements of the same kind that land on each other. Order in the patch,
the DMX address and the fixture name are not used.

A fixture with no placement is not given a partner. A placed fixture with no
reflection in the configuration is an incomplete pair and stays out of Auto.
Two reflections that fit one point are ambiguous: those fixtures are reported
and also stay out. Nothing here is a claim that a lamp was detected on the rig.
"""

from __future__ import annotations

from dataclasses import dataclass

from orng_led.config.models import FixtureKind

AXIS_X = 0.5
MATCH_EPS = 0.02


@dataclass(frozen=True)
class MirrorPair:
    left_id: str
    right_id: str
    kind: FixtureKind
    distance: float


@dataclass(frozen=True)
class MirrorMap:
    pairs: tuple[MirrorPair, ...]
    centers: tuple[str, ...]
    excluded: tuple[str, ...]
    ambiguous: tuple[tuple[str, ...], ...]
    unplaced: tuple[str, ...]


def _point(fixture: object, placements: object) -> tuple[float, float, float] | None:
    fixture_id = str(getattr(fixture, "id", ""))
    if placements is not None:
        for placement in placements:
            if str(getattr(placement, "fixture_id", "")) != fixture_id:
                continue
            return (
                float(placement.x),
                float(placement.y),
                float(getattr(placement, "z", 0.0)),
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
    else:
        raw_z = getattr(fixture, "layout_z", 0.0)
    try:
        return (float(raw_x), float(raw_y), float(raw_z))
    except (TypeError, ValueError):
        return None


def _close(left: tuple[float, float, float], right: tuple[float, float, float]) -> bool:
    return (
        abs(left[0] - right[0]) <= MATCH_EPS
        and abs(left[1] - right[1]) <= MATCH_EPS
        and abs(left[2] - right[2]) <= MATCH_EPS
    )


def _reflection(point: tuple[float, float, float]) -> tuple[float, float, float]:
    return (1.0 - point[0], point[1], point[2])


def resolve_mirrors(fixtures: list[object], placements: object = None) -> MirrorMap:
    """Pair by reflected coordinates. Unmatched and ambiguous ids are not lit."""
    points: dict[str, tuple[FixtureKind, tuple[float, float, float]]] = {}
    unplaced: list[str] = []
    for fixture in fixtures:
        fixture_id = str(getattr(fixture, "id", ""))
        point = _point(fixture, placements)
        if point is None:
            unplaced.append(fixture_id)
            continue
        points[fixture_id] = (fixture.kind, point)

    used: set[str] = set()
    ambiguous: set[str] = set()
    pairs: list[MirrorPair] = []
    for fixture_id, (kind, point) in points.items():
        if fixture_id in used or fixture_id in ambiguous:
            continue
        target = _reflection(point)
        hits = [
            other
            for other, (other_kind, other_point) in points.items()
            if other != fixture_id and other_kind is kind and _close(other_point, target)
        ]
        if len(hits) != 1:
            if hits:
                ambiguous.add(fixture_id)
                ambiguous.update(hits)
            continue
        other = hits[0]
        other_point = points[other][1]
        back = [
            candidate
            for candidate, (candidate_kind, candidate_point) in points.items()
            if candidate != other
            and candidate_kind is kind
            and _close(candidate_point, _reflection(other_point))
        ]
        if back != [fixture_id]:
            ambiguous.add(fixture_id)
            ambiguous.add(other)
            continue
        used.add(fixture_id)
        used.add(other)
        if point[0] <= other_point[0]:
            left, right, left_point = fixture_id, other, point
        else:
            left, right, left_point = other, fixture_id, other_point
        pairs.append(
            MirrorPair(
                left_id=left,
                right_id=right,
                kind=kind,
                distance=abs(left_point[0] - AXIS_X),
            )
        )

    centers: list[str] = []
    excluded: list[str] = []
    for fixture_id, (_, point) in points.items():
        if fixture_id in used or fixture_id in ambiguous:
            continue
        if abs(point[0] - AXIS_X) <= MATCH_EPS:
            centers.append(fixture_id)
        else:
            excluded.append(fixture_id)
    pairs.sort(key=lambda item: (item.kind.value, item.distance, item.left_id))
    grouped: dict[frozenset[str], None] = {}
    for fixture_id in sorted(ambiguous):
        kind, point = points[fixture_id]
        cluster = tuple(
            sorted(
                other
                for other, (other_kind, other_point) in points.items()
                if other_kind is kind and (
                    other == fixture_id or _close(other_point, _reflection(point))
                )
            )
        )
        grouped[frozenset(cluster)] = None
    return MirrorMap(
        pairs=tuple(pairs),
        centers=tuple(centers),
        excluded=tuple(sorted(excluded)),
        ambiguous=tuple(tuple(sorted(cluster)) for cluster in grouped),
        unplaced=tuple(unplaced),
    )
