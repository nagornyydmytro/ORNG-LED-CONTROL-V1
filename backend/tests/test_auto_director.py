"""Auto composes inside a theme. It does not type on the operator's keys."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from orng_led.api.runtime import AppRuntime
from orng_led.audio.auto_show import build_auto_stage
from orng_led.config.models import FixtureKind
from orng_led.engine.intents import BeamIntent, ParIntent
from orng_led.input.contract import InputAction, InputEvent, InputSource
from orng_led.input.mapping import button_to_event

SOURCES = (
    ("linux-pad", InputSource.KEYBOARD),
    ("win-keys", InputSource.KEYBOARD),
    ("web-otg", InputSource.KEYBOARD),
    ("win-media", InputSource.KEYBOARD),
    ("cloud:desk:remote", InputSource.UI),
    ("phone", InputSource.UI),
)


def _press(action: InputAction, source: InputSource, source_id: str) -> InputEvent:
    return InputEvent(action, source, source_id=source_id, edge="press")


class _Spot:
    def __init__(self, side: str, order: int, invert: bool = False, segments: int | None = None) -> None:
        self.side = side
        self.order = order
        self.invert_segments = invert
        self.segment_count = segments


class _Lamp:
    def __init__(self, lamp_id: str, kind: FixtureKind, *, segments: int | None = None) -> None:
        self.id = lamp_id
        self.kind = kind
        self.spatial = _Spot("center", 1, False, segments)
        self.segment_count = segments
        self.layout_x = 0.5
        self.layout_y = 0.5


def test_cycles_follow_beats_and_themes_limit_the_look(tmp_path: Path) -> None:
    lamps = [
        _Lamp("par", FixtureKind.PAR),
        _Lamp("bar", FixtureKind.BAR, segments=8),
        _Lamp("beam", FixtureKind.BEAM),
        _Lamp("face", FixtureKind.FACE_PAR),
    ]
    lamps[0].layout_x = 0.25
    lamps[1].layout_x = 0.50
    lamps[2].layout_x = 0.78
    quiet = build_auto_stage(
        lamps, "orange", "groove", 0.0, 0.8, pattern=0, beats=0.0, cycle_beats=4
    )
    later = build_auto_stage(
        lamps, "orange", "groove", 0.0, 0.8, pattern=0, beats=4.0, cycle_beats=4
    )
    mid = build_auto_stage(
        lamps, "orange", "groove", 0.0, 0.8, pattern=0, beats=1.0, cycle_beats=4
    )
    assert quiet.fixtures["par"].intensity == pytest.approx(later.fixtures["par"].intensity)
    assert quiet.fixtures["par"].intensity != pytest.approx(mid.fixtures["par"].intensity)
    assert "face" not in quiet.fixtures
    beam = quiet.fixtures["beam"]
    assert isinstance(beam, BeamIntent)
    assert beam.dimmer == pytest.approx(0.8)
    assert beam.pan is None and beam.strobe == 0.0
    bars = build_auto_stage(lamps, "bars", "groove", 0.2, 0.9, beats=1.0, cycle_beats=2)
    assert set(bars.fixtures) == {"bar"}
    heads = build_auto_stage(lamps, "heads", "groove", 0.2, 1.0, beats=0.0, cycle_beats=4)
    assert "bar" not in heads.fixtures and "face" not in heads.fixtures
    assert heads.fixtures["par"].intensity <= 0.8
    sparse = build_auto_stage(
        lamps, "orange", "groove", 0.2, 0.8, look="groups", beats=0.0, cycle_beats=2
    )
    assert "beam" not in sparse.fixtures
    assert "par" in sparse.fixtures

    rows = []
    for cycle in (1, 2, 4):
        row = []
        for step in range(96):
            stage = build_auto_stage(
                lamps,
                "red",
                "groove",
                0.0,
                0.9,
                pattern=0,
                beats=8.0 * step / 96.0,
                cycle_beats=cycle,
            )
            intent = stage.fixtures["par"]
            assert isinstance(intent, ParIntent)
            row.append(int(round(intent.intensity * 255)))
        rows.append(row)
    path = tmp_path / "auto-director.bmp"
    _write_bmp(path, rows)
    assert path.stat().st_size > 54


def test_auto_entry_releases_holds_and_blocks_every_executor_input(tmp_path: Path) -> None:
    runtime = AppRuntime.create(autostart_loop=False, config_dir=tmp_path)
    preset = runtime.engine.active_preset_id
    folder = Path(runtime.config_dir)
    before = {
        path: path.read_bytes()
        for path in folder.rglob("*.yaml")
        if path.parent.name == "presets"
    }
    dispatcher = runtime.input_dispatcher
    assert dispatcher is not None
    runtime.engine.set_blackout(False)
    held = dispatcher.dispatch(_press(InputAction.STROBE_PRESS, InputSource.KEYBOARD, "linux-pad"))
    assert held.accepted is True
    assert runtime.engine.overlays.strobe_held is True
    runtime.engine.set_face(True)
    runtime.apply_bpm_sync(enabled=True, scope="auto", palette="orange")
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.engine.overlays.face_on is True
    assert runtime.engine.active_preset_id == preset
    assert runtime.bpm.scope == "auto"

    live_actions = (
        InputAction.SWEEP_PRESS,
        InputAction.LIVE_FX_SPEED_UP,
        InputAction.PROGRAM_SPEED_UP,
    )
    face = dispatcher.dispatch(_press(InputAction.FACE_TOGGLE, InputSource.KEYBOARD, "linux-pad"))
    assert face.accepted is True
    blocked_actions = (
        InputAction.SELECT_PRESET,
        InputAction.EPISODE_NEXT,
    )
    for source_id, source in SOURCES:
        for action in live_actions:
            result = dispatcher.dispatch(_press(action, source, source_id))
            assert result.accepted is True, (action, result.reason)
        for action in blocked_actions:
            result = dispatcher.dispatch(_press(action, source, source_id))
            assert result.accepted is False
            assert result.reason == "auto_keyboard_blocked"
        pad = button_to_event(1, source=source, source_id=source_id)
        assert dispatcher.dispatch(pad).accepted is False

    blackout = button_to_event(14, source=InputSource.KEYBOARD, source_id="ch57x")
    assert blackout.action is InputAction.BLACKOUT_TOGGLE
    assert dispatcher.dispatch(blackout).accepted is True
    phone = dispatcher.dispatch(
        _press(InputAction.BLACKOUT_TOGGLE, InputSource.UI, "phone")
    )
    assert phone.accepted is True

    runtime.bpm.follower.bpm = None
    runtime.bpm.follower.locked = False
    runtime.bpm.follower._started = False
    runtime.bpm.capture = "no_device"
    runtime.bpm.section.lost_signal()
    runtime.tick(0.05)
    assert runtime.bpm.scope == "auto"
    assert runtime.bpm.enabled is True
    stage = runtime.engine.auto_base
    assert stage is not None
    for intent in stage.fixtures.values():
        assert getattr(intent, "strobe", 0.0) == 0.0
        if isinstance(intent, BeamIntent):
            assert intent.pan is None and intent.dimmer in {0.0, 1.0}
    assert runtime.engine.overlays.sweep_held is True
    extra = dispatcher.dispatch(_press(InputAction.SWEEP_PRESS, InputSource.KEYBOARD, "extra-sweep"))
    assert extra.accepted is True

    runtime.apply_bpm_sync(scope="preset")
    assert runtime.engine.auto_base is None
    assert runtime.engine.active_preset_id == preset
    assert runtime.engine.overlays.strobe_held is True
    dispatcher.dispatch(
        InputEvent(InputAction.STROBE_RELEASE, InputSource.KEYBOARD, source_id="linux-pad", edge="release")
    )
    fresh = dispatcher.dispatch(_press(InputAction.STROBE_PRESS, InputSource.KEYBOARD, "linux-pad"))
    assert fresh.accepted is True
    assert runtime.engine.overlays.strobe_held is True
    assert runtime.engine.bpm_fx is False
    after = {
        path: path.read_bytes()
        for path in folder.rglob("*.yaml")
        if path.parent.name == "presets"
    }
    assert after == before


def _write_bmp(path: Path, rows: list[list[int]]) -> None:
    width = len(rows[0])
    scale = 3
    band = 16
    gap = 6
    height = len(rows) * (band + gap)
    image_width = width * scale
    pixels = bytearray(image_width * height * 3)
    for row_index, row in enumerate(rows):
        for x, value in enumerate(row):
            level = max(0, min(255, value))
            red, green, blue = level, int(level * 0.32), int(level * 0.05)
            y0 = row_index * (band + gap)
            for sy in range(band):
                y = height - 1 - (y0 + sy)
                for sx in range(scale):
                    offset = (y * image_width + x * scale + sx) * 3
                    pixels[offset : offset + 3] = bytes((blue, green, red))
    row_bytes = image_width * 3
    stride = (row_bytes + 3) & ~3
    raw = bytearray()
    for y in range(height):
        start = y * row_bytes
        raw.extend(pixels[start : start + row_bytes])
        raw.extend(b"\x00" * (stride - row_bytes))
    header = struct.pack("<2sIHHI", b"BM", 54 + len(raw), 0, 0, 54)
    info = struct.pack("<IIIHHIIIIII", 40, image_width, height, 1, 24, 0, len(raw), 0, 0, 0, 0)
    path.write_bytes(header + info + raw)
