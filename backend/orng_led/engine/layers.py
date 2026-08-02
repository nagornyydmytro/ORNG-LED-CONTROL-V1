"""Layer composition.

Priority, lowest first::

    preset → sweep → colour hit → white hit → strobe → drop
           → face → master → blackout

Blackout is not a layer here: the engine zeroes the *base* before composition,
so Live Effects still compose on top. Absolute physical stop is Disarm / Art-Net
gates — not Blackout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from orng_led.config.models import FixtureInstance, FixtureKind, ShowConfig
from orng_led.engine.intents import BarIntent, BeamIntent, ParIntent, Rgbw, StageIntent

WHITE_HIT_DURATION_S = 0.18
STROBE_MAX_HZ = 4.0
STROBE_HOLD_TIMEOUT_S = 8.0

# Quick live effects (canon §5.3 momentary rules).
DROP_MAX_DURATION_S = 8.0
COLOR_HIT_DURATION_S = 0.35
SWEEP_HOLD_TIMEOUT_S = 8.0
SWEEP_WIDTH = 0.22
# Sweep cycle period at speed=0.5; faster speeds shorten the period.
SWEEP_PERIOD_SLOW_S = 2.4
SWEEP_PERIOD_FAST_S = 0.28

WHITE = Rgbw(r=1.0, g=1.0, b=1.0)

SweepMode = Literal["horizontal", "vertical"]

# Live-effect identity for debug / UI (not tied to nonzero preset channels).
LIVE_EFFECT_META: dict[str, dict[str, object]] = {
    "white_hit": {
        "label": "White Hit",
        "target_groups": ["rear", "all_rear", "par", "bar", "beam"],
    },
    "color_hit": {
        "label": "Color Hit",
        "target_groups": ["rear", "all_rear", "par", "bar", "beam"],
    },
    "sweep_hit": {
        "label": "Sweep",
        "target_groups": ["rear", "all_rear", "par", "bar", "beam"],
    },
    "vertical_sweep": {
        "label": "Vertical Sweep",
        "target_groups": ["rear", "all_rear", "par", "bar", "beam"],
    },
    "strobe": {
        "label": "Live Strobe",
        "target_groups": ["rear", "all_rear", "par", "bar", "beam"],
    },
    "drop": {
        "label": "Drop",
        "target_groups": ["rear", "all_rear", "par", "bar", "beam"],
    },
    "face": {
        "label": "DJ Face",
        "target_groups": ["face", "face_par"],
    },
}


@dataclass
class OverlayState:
    white_hit_until: float | None = None
    strobe_held: bool = False
    strobe_started_at: float | None = None
    strobe_speed: float = 0.7
    face_on: bool = False
    face_brightness: float = 0.55
    master_brightness: float = 1.0
    blackout: bool = False
    # Quick live effects.
    drop_held: bool = False
    drop_started_at: float | None = None
    color_hit_until: float | None = None
    color_hit_color: Rgbw = field(default_factory=lambda: Rgbw(r=1.0, g=1.0, b=1.0))
    sweep_held: bool = False
    sweep_started_at: float | None = None
    sweep_mode: SweepMode = "horizontal"
    sweep_speed: float = 0.7
    sweep_color: Rgbw = field(default_factory=lambda: Rgbw(r=1.0, g=1.0, b=1.0))


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _strobe_active(overlays: OverlayState, now: float) -> bool:
    if not overlays.strobe_held or overlays.strobe_started_at is None:
        return False
    if now - overlays.strobe_started_at >= STROBE_HOLD_TIMEOUT_S:
        return False
    return overlays.strobe_speed > 0.0


def drop_active(overlays: OverlayState, now: float) -> bool:
    if not overlays.drop_held or overlays.drop_started_at is None:
        return False
    return now - overlays.drop_started_at < DROP_MAX_DURATION_S


def _sweep_active(overlays: OverlayState, now: float) -> bool:
    if not overlays.sweep_held or overlays.sweep_started_at is None:
        return False
    if now - overlays.sweep_started_at >= SWEEP_HOLD_TIMEOUT_S:
        return False
    return overlays.sweep_speed > 0.0


def sweep_progress(overlays: OverlayState, now: float) -> float | None:
    """Return looping 0..1 progress while sweep is held, or None when inactive.

    At speed ≈ 1.0 the stage is fully filled (progress sentinel 1.0 with full look).
    """
    if not _sweep_active(overlays, now):
        return None
    speed = _clamp01(overlays.sweep_speed)
    if speed >= 0.999:
        return 1.0
    # Map mid speeds to a looping wavefront.
    period = SWEEP_PERIOD_SLOW_S + (SWEEP_PERIOD_FAST_S - SWEEP_PERIOD_SLOW_S) * speed
    started = overlays.sweep_started_at
    elapsed = max(0.0, now - float(started if started is not None else now))
    return (elapsed / max(0.05, period)) % 1.0


def _strobe_gate(now: float, speed: float) -> float:
    """0 = off, 1 = solid white, mid = blink rate up to STROBE_MAX_HZ."""
    speed = _clamp01(speed)
    if speed <= 0.0:
        return 0.0
    if speed >= 0.999:
        return 1.0
    hz = max(0.25, STROBE_MAX_HZ * speed)
    phase = math.floor(now * hz * 2) % 2
    return 1.0 if phase == 0 else 0.0


def _is_rear(kind: FixtureKind) -> bool:
    return kind is not FixtureKind.FACE_PAR


def rear_fixture_ids(show: ShowConfig) -> list[str]:
    return [fx.id for fx in show.patch.fixtures if _is_rear(fx.kind)]


def face_fixture_ids(show: ShowConfig) -> list[str]:
    return [fx.id for fx in show.patch.fixtures if fx.kind is FixtureKind.FACE_PAR]


def _force_rear_look(
    stage: StageIntent,
    fixture: FixtureInstance,
    *,
    color: Rgbw,
    level: float,
    strobe: float = 0.0,
    pan: float | None = None,
    tilt: float | None = None,
    segments: tuple[float, ...] | None = None,
) -> None:
    """Independent full look for every rear fixture — never gated by the preset.

    Beam Live Effects normally leave Pan/Tilt alone (None). Vertical Sweep may
    pass an explicit tilt while keeping the previous pan axis.
    """
    level = max(0.0, min(1.0, level))
    if fixture.kind is FixtureKind.PAR:
        stage.fixtures[fixture.id] = ParIntent(color=color, intensity=level, strobe=strobe)
    elif fixture.kind is FixtureKind.BAR:
        if segments is None:
            segs = (level,) * 8 if level > 0 else (0.0,) * 8
        else:
            segs = tuple(max(0.0, min(1.0, s)) for s in segments[:8])
            if len(segs) < 8:
                segs = segs + (0.0,) * (8 - len(segs))
        dimmer = max(segs) if segs else level
        stage.fixtures[fixture.id] = BarIntent(
            segments=segs,
            dimmer=dimmer,
            color=color,
            strobe=strobe,
            whole=False,
        )
    elif fixture.kind is FixtureKind.BEAM:
        on = level > 0.02
        stage.fixtures[fixture.id] = BeamIntent(
            pan=pan,
            tilt=tilt,
            dimmer=1.0 if on else 0.0,
            color=color,
            shutter_open=on,
            strobe=strobe,
        )


def apply_white_hit(stage: StageIntent, show: ShowConfig) -> StageIntent:
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        _force_rear_look(stage, fixture, color=WHITE, level=1.0)
    return stage


def apply_color_hit(stage: StageIntent, show: ShowConfig, color: Rgbw) -> StageIntent:
    """Short, strong RGB accent — full independent replace for every rear fixture."""
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        _force_rear_look(stage, fixture, color=color, level=1.0)
    return stage


def _fixture_axis(show: ShowConfig, fixture: FixtureInstance, axis: str) -> float:
    placement = show.layout.placement_for(fixture.id)
    if placement is not None:
        return float(getattr(placement, axis))
    order = fixture.spatial.order or 1
    return (order - 1) / 3.0


def apply_sweep_hit(
    stage: StageIntent,
    show: ShowConfig,
    progress: float,
    color: Rgbw,
) -> StageIntent:
    """Hold sweep across the semantic layout, left → right by stage x."""
    if progress >= 0.999:
        for fixture in show.patch.fixtures:
            if not _is_rear(fixture.kind):
                continue
            _force_rear_look(stage, fixture, color=color, level=1.0)
        return stage

    front = -SWEEP_WIDTH + progress * (1.0 + 2.0 * SWEEP_WIDTH)
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        position = _fixture_axis(show, fixture, "x")
        distance = abs(position - front)
        if distance >= SWEEP_WIDTH:
            continue
        boost = 1.0 - (distance / SWEEP_WIDTH)
        _force_rear_look(stage, fixture, color=color, level=boost)
    return stage


def apply_vertical_sweep(
    stage: StageIntent,
    show: ShowConfig,
    progress: float,
    color: Rgbw,
) -> StageIntent:
    """Hold sweep bottom → top: bar segments climb; beams tilt up, pan kept."""
    full = progress >= 0.999
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue

        if fixture.kind is FixtureKind.BAR:
            if full:
                segments = (1.0,) * 8
            else:
                # Segment 0 = bottom, 7 = top.
                head = progress * 8.0
                segments_list: list[float] = []
                for index in range(8):
                    distance = abs(index + 0.5 - head)
                    segments_list.append(max(0.0, 1.0 - distance / 1.6))
                segments = tuple(segments_list)
            _force_rear_look(stage, fixture, color=color, level=max(segments), segments=segments)
            continue

        if fixture.kind is FixtureKind.BEAM:
            prev = stage.fixtures.get(fixture.id)
            pan = prev.pan if isinstance(prev, BeamIntent) else None
            tilt = 0.12 + 0.76 * (1.0 if full else progress)
            level = 1.0 if full else max(0.35, 0.55 + 0.45 * math.sin(progress * math.pi))
            _force_rear_look(
                stage,
                fixture,
                color=color,
                level=level,
                pan=pan,
                tilt=max(0.0, min(1.0, tilt)),
            )
            continue

        # PARs / others: soft rise by layout y.
        if full:
            _force_rear_look(stage, fixture, color=color, level=1.0)
            continue
        position = _fixture_axis(show, fixture, "y")
        front = -SWEEP_WIDTH + progress * (1.0 + 2.0 * SWEEP_WIDTH)
        distance = abs(position - front)
        if distance >= SWEEP_WIDTH:
            continue
        boost = 1.0 - (distance / SWEEP_WIDTH)
        _force_rear_look(stage, fixture, color=color, level=boost)
    return stage


def apply_drop(stage: StageIntent, show: ShowConfig) -> StageIntent:
    """Momentary kill of the rear stage look; the preset clock keeps running."""
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        if fixture.kind is FixtureKind.PAR:
            stage.fixtures[fixture.id] = ParIntent(
                color=Rgbw(),
                intensity=0.0,
                strobe=0.0,
            )
        elif fixture.kind is FixtureKind.BAR:
            stage.fixtures[fixture.id] = BarIntent(
                segments=(0.0,) * 8,
                dimmer=0.0,
                color=Rgbw(),
                strobe=0.0,
                whole=False,
            )
        elif fixture.kind is FixtureKind.BEAM:
            stage.fixtures[fixture.id] = BeamIntent(
                pan=None,
                tilt=None,
                dimmer=0.0,
                color=Rgbw(),
                shutter_open=False,
                strobe=0.0,
            )
        else:
            stage.fixtures.pop(fixture.id, None)
    return stage


def apply_strobe(stage: StageIntent, show: ShowConfig, now: float, speed: float) -> StageIntent:
    """Independent full-scene white strobe. Speed 0=off … 1=solid white."""
    gate = _strobe_gate(now, speed)
    level = 1.0 if gate > 0 else 0.0
    strobe = 0.85 if 0.0 < gate < 1.0 else 0.0
    for fixture in show.patch.fixtures:
        if not _is_rear(fixture.kind):
            continue
        _force_rear_look(stage, fixture, color=WHITE, level=level, strobe=strobe)
    return stage


def apply_face(stage: StageIntent, show: ShowConfig, overlays: OverlayState) -> StageIntent:
    for fixture in show.patch.fixtures:
        if fixture.kind is not FixtureKind.FACE_PAR:
            continue
        if overlays.face_on:
            stage.fixtures[fixture.id] = ParIntent(
                color=Rgbw(r=1.0, g=0.92, b=0.75, w=0.8),
                intensity=overlays.face_brightness,
            )
        else:
            stage.fixtures.pop(fixture.id, None)
    return stage


def apply_master_brightness(stage: StageIntent, master: float) -> StageIntent:
    """Master is applied in the renderer on DIMMER roles only — keep intents intact."""
    _ = master
    return stage


def active_live_effect_ids(overlays: OverlayState, now: float) -> list[str]:
    active: list[str] = []
    if _sweep_active(overlays, now):
        if overlays.sweep_mode == "vertical":
            active.append("vertical_sweep")
        else:
            active.append("sweep_hit")
    if overlays.color_hit_until is not None and now < overlays.color_hit_until:
        active.append("color_hit")
    if overlays.white_hit_until is not None and now < overlays.white_hit_until:
        active.append("white_hit")
    if _strobe_active(overlays, now):
        active.append("strobe")
    if drop_active(overlays, now):
        active.append("drop")
    if overlays.face_on:
        active.append("face")
    return active


def target_fixture_ids_for_effect(effect_id: str, show: ShowConfig) -> list[str]:
    if effect_id == "face":
        return face_fixture_ids(show)
    if effect_id in LIVE_EFFECT_META:
        return rear_fixture_ids(show)
    return []


def compose_layers(
    base: StageIntent,
    show: ShowConfig,
    overlays: OverlayState,
    now: float,
) -> StageIntent:
    stage = StageIntent(fixtures=dict(base.fixtures))

    progress = sweep_progress(overlays, now)
    if progress is not None:
        if overlays.sweep_mode == "vertical":
            stage = apply_vertical_sweep(stage, show, progress, overlays.sweep_color)
        else:
            stage = apply_sweep_hit(stage, show, progress, overlays.sweep_color)

    if overlays.color_hit_until is not None and now < overlays.color_hit_until:
        stage = apply_color_hit(stage, show, overlays.color_hit_color)

    if overlays.white_hit_until is not None and now < overlays.white_hit_until:
        stage = apply_white_hit(stage, show)

    if _strobe_active(overlays, now):
        stage = apply_strobe(stage, show, now, overlays.strobe_speed)

    if drop_active(overlays, now):
        stage = apply_drop(stage, show)

    stage = apply_face(stage, show, overlays)
    stage = apply_master_brightness(stage, overlays.master_brightness)
    return stage
