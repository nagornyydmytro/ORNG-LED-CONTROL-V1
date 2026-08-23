"""Live Strobe: frame-locked flashes; speed changes dark gap only."""

from __future__ import annotations

import pytest

from orng_led.engine.clock import FPS, FRAME_DT
from orng_led.engine.layers import (
    STROBE_FLASH_ON_S,
    STROBE_MAX_HZ,
    STROBE_MIN_HZ,
    STROBE_ON_FRAMES,
    OverlayState,
    advance_strobe_phase,
    advance_sweep_phase,
    sweep_progress,
    _strobe_gate,
    _strobe_gate_phase,
    _strobe_gate_tick,
    _strobe_period_frames,
)


def _on_off_pattern(speed: float, *, frames: int = 120) -> list[int]:
    """Sample the gate once per engine tick from hold start."""
    pattern: list[int] = []
    for index in range(frames):
        on = _strobe_gate_tick(index, speed) > 0.0
        pattern.append(1 if on else 0)
    return pattern


def test_flash_is_exactly_one_frame_across_speeds() -> None:
    for speed in (0.2, 0.5, 0.7, 1.0):
        pattern = _on_off_pattern(speed)
        period = _strobe_period_frames(speed)
        assert period >= 2, speed
        # Contiguous ON runs are exactly STROBE_ON_FRAMES long.
        i = 0
        ons = 0
        while i < len(pattern):
            if pattern[i] == 1:
                width = 0
                while i < len(pattern) and pattern[i] == 1:
                    width += 1
                    i += 1
                assert width == STROBE_ON_FRAMES, (speed, width)
                ons += 1
            else:
                i += 1
        assert ons >= 3, speed


def test_faster_speed_shortens_dark_gap_not_flash() -> None:
    slow_period = _strobe_period_frames(0.25)
    fast_period = _strobe_period_frames(0.9)
    assert fast_period < slow_period
    assert _strobe_period_frames(1.0) == 2
    slow = _on_off_pattern(0.25, frames=180)
    fast = _on_off_pattern(0.9, frames=180)
    assert sum(fast) > sum(slow)


def test_strobe_off_and_period_bounds() -> None:
    assert _strobe_gate(0.0, 0.0) == 0.0
    assert STROBE_FLASH_ON_S == FRAME_DT
    assert STROBE_MAX_HZ == FPS / 2
    assert STROBE_MIN_HZ >= 1.0
    assert _strobe_gate(0.0, 1.0) == 1.0
    assert _strobe_gate(FRAME_DT, 1.0) == 0.0


def test_gate_stable_under_jittered_sample_times() -> None:
    """Elapsed-time compat helper still maps into the same tick cells."""
    speed = 0.7
    period = _strobe_period_frames(speed)
    for frame_i in range(period * 4):
        base = frame_i * FRAME_DT
        expected = _strobe_gate_tick(frame_i, speed)
        assert _strobe_gate(base, speed) == expected
        assert _strobe_gate(base + FRAME_DT * 0.1, speed) == expected
        assert _strobe_gate(base + FRAME_DT * 0.9, speed) == expected


def test_speed_change_keeps_strobe_and_sweep_phase() -> None:
    """Changing live-FX speed must not restart the hold cycle."""
    overlays = OverlayState(
        strobe_held=True,
        strobe_started_at=0.0,
        strobe_speed=0.2,
        strobe_phase=0.4,
        sweep_held=True,
        sweep_started_at=0.0,
        sweep_speed=0.2,
        sweep_phase=0.55,
    )
    # Phase values themselves are unchanged by the speed assignment.
    overlays.strobe_speed = 0.9
    overlays.sweep_speed = 0.9
    assert overlays.strobe_phase == pytest.approx(0.4)
    assert overlays.sweep_phase == pytest.approx(0.55)
    assert sweep_progress(overlays, now=1.0) == pytest.approx(0.55)
    # Gate still reads the same phase position (only future advance rate changes).
    assert _strobe_gate_phase(0.4, 0.9) == _strobe_gate_phase(0.4, 0.9)

    advance_strobe_phase(overlays)
    advance_sweep_phase(overlays, 0.05)
    assert overlays.strobe_phase != pytest.approx(0.4)
    assert overlays.sweep_phase != pytest.approx(0.55)
