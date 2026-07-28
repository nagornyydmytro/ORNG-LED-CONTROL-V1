"""Stage geometry must match the operator reference sketch (HOME correction)."""

from __future__ import annotations

from orng_led.config import load_show_config
from orng_led.config.models import MountPosition, Orientation
from orng_led.simulator.geometry import beam_direction, beam_vector


def _placements() -> dict:
    layout = load_show_config().layout
    return {p.fixture_id: p for p in layout.placements}


def test_layout_covers_every_patched_fixture() -> None:
    show = load_show_config()
    placements = _placements()
    patch_ids = {fx.id for fx in show.patch.fixtures}
    assert set(placements) == patch_ids
    assert len(patch_ids) == 12


def test_reference_counts_par_bar_beam() -> None:
    show = load_show_config()
    kinds: dict[str, int] = {}
    for fixture in show.patch.fixtures:
        kinds[fixture.kind.value] = kinds.get(fixture.kind.value, 0) + 1
    # Sketch: 6 × PAR 36 (4 rear + 2 face), 4 × Bar 124, 2 × LED Beam.
    assert kinds["par"] + kinds["face_par"] == 6
    assert kinds["bar"] == 4
    assert kinds["beam"] == 2


def test_bars_are_vertical_in_one_centre_row() -> None:
    placements = _placements()
    bars = [placements[f"bar_{i}"] for i in range(1, 5)]
    assert all(bar.orientation is Orientation.VERTICAL for bar in bars)
    assert all(bar.height > bar.width * 5 for bar in bars)
    # One horizontal row: same y, strictly increasing x left → right.
    assert len({round(bar.y, 3) for bar in bars}) == 1
    xs = [bar.x for bar in bars]
    assert xs == sorted(xs)
    gaps = [round(b - a, 3) for a, b in zip(xs, xs[1:], strict=False)]
    assert max(gaps) - min(gaps) < 0.01  # evenly spaced
    assert abs((xs[0] + xs[-1]) / 2 - 0.5) < 0.01  # symmetric around centre


def test_beams_sit_on_top_centre_left_and_right() -> None:
    placements = _placements()
    left = placements["beam_left"]
    right = placements["beam_right"]
    bars = [placements[f"bar_{i}"] for i in range(1, 5)]
    bottom_pars = [placements[f"par_{i}"] for i in range(1, 5)]

    assert left.x < 0.5 < right.x
    assert abs((left.x + right.x) / 2 - 0.5) < 0.01
    assert left.mount is MountPosition.CEILING
    assert right.mount is MountPosition.CEILING
    # Beams above the Bars, Bars above the bottom PAR row.
    assert max(left.y, right.y) < min(bar.y for bar in bars)
    assert max(bar.y for bar in bars) < min(par.y for par in bottom_pars)
    # Beams are between the top corner PARs, not in the corners themselves.
    assert placements["face_par_1"].x < left.x
    assert placements["face_par_2"].x > right.x


def test_bottom_par_row_is_a_shallow_u() -> None:
    placements = _placements()
    outer_left = placements["par_1"]
    inner_left = placements["par_2"]
    inner_right = placements["par_3"]
    outer_right = placements["par_4"]

    # y grows downwards: the inner pair hangs lower than the outer pair.
    assert inner_left.y > outer_left.y
    assert inner_right.y > outer_right.y
    assert abs(inner_left.y - inner_right.y) < 1e-6
    assert abs(outer_left.y - outer_right.y) < 1e-6
    # Left → right ordering and mirror symmetry.
    assert outer_left.x < inner_left.x < inner_right.x < outer_right.x
    assert abs((outer_left.x + outer_right.x) / 2 - 0.5) < 0.01
    assert abs((inner_left.x + inner_right.x) / 2 - 0.5) < 0.01


def test_face_pars_are_the_top_outer_corners() -> None:
    placements = _placements()
    left = placements["face_par_1"]
    right = placements["face_par_2"]
    others = [p for fid, p in placements.items() if fid not in {"face_par_1", "face_par_2"}]
    assert left.x == min(p.x for p in placements.values())
    assert right.x == max(p.x for p in placements.values())
    assert left.y < max(p.y for p in others)
    assert abs((left.x + right.x) / 2 - 0.5) < 0.01


def test_cable_chain_follows_the_sketch() -> None:
    layout = load_show_config().layout
    assert layout.cable_chain == [
        "par_1",
        "par_2",
        "par_3",
        "par_4",
        "beam_right",
        "beam_left",
        "bar_1",
        "bar_2",
        "bar_3",
        "bar_4",
        "face_par_2",
        "face_par_1",
    ]
    assert layout.artnet_node is not None
    assert layout.artnet_node.x < 0.3
    assert layout.artnet_node.y > 0.8


def test_layout_is_never_marked_hardware_verified() -> None:
    show = load_show_config()
    assert "PENDING HARDWARE" in show.layout.description
    assert all(profile.hardware_verified is False for profile in show.profiles.values())


def test_beam_direction_is_a_unit_3d_vector() -> None:
    dx, dy, dz = beam_direction(0.5, 0.0)
    assert abs(dx) < 1e-9
    assert abs(dz) < 1e-9
    assert dy == -1.0  # tilt 0 → straight down

    for pan in (0.0, 0.25, 0.5, 0.75, 1.0):
        for tilt in (0.0, 0.3, 0.5, 0.8, 1.0):
            x, y, z = beam_direction(pan, tilt)
            assert abs((x * x + y * y + z * z) ** 0.5 - 1.0) < 1e-9


def test_beam_hit_point_moves_with_pan_and_tilt() -> None:
    origin = {"origin_x": 0.4, "origin_y": 0.75, "origin_z": 0.85}
    centre = beam_vector(pan=0.5, tilt=0.35, **origin)
    left = beam_vector(pan=0.2, tilt=0.35, **origin)
    right = beam_vector(pan=0.8, tilt=0.35, **origin)

    assert left.hit_x < centre.hit_x < right.hit_x
    # Left/right stays consistent at every tilt (no accidental mirroring).
    for tilt in (0.1, 0.35, 0.6, 0.9):
        low = beam_vector(pan=0.25, tilt=tilt, **origin)
        high = beam_vector(pan=0.75, tilt=tilt, **origin)
        assert low.hit_x < high.hit_x, tilt
    # Tilting further from vertical throws the spot further away.
    steep = beam_vector(pan=0.5, tilt=0.05, **origin)
    shallow = beam_vector(pan=0.5, tilt=0.6, **origin)
    assert shallow.throw > steep.throw
